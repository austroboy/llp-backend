"""Mark older amendment nodes as superseded by newer ones.

When two amendment documents both amend the same section of the parent Act,
only the latest one should be cited. This command scans every amendment
document's nodes, extracts the parent-Act section number being amended
(from text like "Amendment of section 264 of Act No. 42 of 2006"), and
flags older amendments' nodes that touch the same section with
`supersession = {"status": "superseded", "superseded_by": "<doc_code>"}`.

The retrieval layer already demotes nodes carrying this flag (see
`apps.documents.retrieval._demote_superseded`), so no other code changes
are needed for the citation behaviour to improve immediately.

Run:
    python manage.py mark_superseded
    python manage.py mark_superseded --dry-run    # report only
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.documents.models import Document, Node

logger = logging.getLogger(__name__)

# Match every flavour the OCR produces:
#   "Amendment of section 264 of Act No. 42 of 2006"
#   "Amendment of section 286"
#   "section 190 of the said Act"
#   "for section 345 of the said Act"
#   "## 43. Amendment of section 190"
_AMENDMENT_TARGET_PATTERNS = [
    re.compile(r"Amendment of section\s+(\d+[A-Za-z]?)", re.IGNORECASE),
    re.compile(r"Insertion of (?:new\s+)?section\s+(\d+[A-Za-z]?)", re.IGNORECASE),
    re.compile(r"Substitution of section\s+(\d+[A-Za-z]?)", re.IGNORECASE),
    re.compile(r"Omission of section\s+(\d+[A-Za-z]?)", re.IGNORECASE),
    re.compile(r"for section\s+(\d+[A-Za-z]?)\s+of\s+the\s+said\s+Act", re.IGNORECASE),
    re.compile(r"In section\s+(\d+[A-Za-z]?)\s+of\s+the\s+said\s+Act", re.IGNORECASE),
]


def _extract_target_sections(text: str) -> set[str]:
    """Pull out all parent-Act section numbers this amendment text touches."""
    targets: set[str] = set()
    if not text:
        return targets
    for pattern in _AMENDMENT_TARGET_PATTERNS:
        for m in pattern.finditer(text):
            targets.add(m.group(1).strip())
    return targets


class Command(BaseCommand):
    help = "Mark older amendment nodes as superseded when a newer amendment touches the same section."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be marked without writing to the DB.",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]

        # Step 1 — load all amendment / ordinance documents in chronological order.
        amendments = list(
            Document.objects.filter(
                instrument_type__in=["Amendment Act", "Amendment Rules", "Ordinance"],
            ).order_by("date_enacted", "doc_code")
        )
        if not amendments:
            self.stdout.write(self.style.WARNING("No amendment documents found."))
            return

        self.stdout.write(f"Found {len(amendments)} amendment documents:")
        for doc in amendments:
            self.stdout.write(
                f"  {doc.doc_code}  {doc.date_enacted or 'no-date'}  {doc.title}"
            )
        self.stdout.write("")

        # Step 2 — build a {(parent_act_section) → [(doc_code, node_id, date)]} map.
        # Each entry says "this section is amended by these (doc, node) pairs".
        section_touches: dict[str, list[tuple[str, str, date | None]]] = defaultdict(list)

        amend_docs_by_code = {d.doc_code: d for d in amendments}
        all_amend_nodes = (
            Node.objects.filter(
                doc_code__in=list(amend_docs_by_code.keys()),
                version__is_current=True,
            )
            .values("node_id", "doc_code", "content", "title")
        )

        for n in all_amend_nodes:
            haystack = f"{n['title'] or ''}\n{n['content'] or ''}"
            targets = _extract_target_sections(haystack)
            if not targets:
                continue
            doc = amend_docs_by_code[n["doc_code"]]
            for sec in targets:
                section_touches[sec].append(
                    (n["doc_code"], n["node_id"], doc.date_enacted)
                )

        if not section_touches:
            self.stdout.write(self.style.WARNING(
                "No amendment-target sections extracted. Check OCR quality."
            ))
            return

        # Step 3 — for each parent-Act section touched by 2+ amendments, the
        # latest one wins. Mark all older ones as superseded.
        nodes_to_mark: dict[str, dict] = {}  # node_id → supersession dict

        for sec, touches in section_touches.items():
            if len(touches) < 2:
                continue
            # sort by date_enacted (None goes last so dated docs win)
            ordered = sorted(
                touches,
                key=lambda t: (t[2] is None, t[2] or date.min),
            )
            *older, latest = ordered
            latest_doc_code = latest[0]
            for older_doc_code, older_node_id, _older_date in older:
                # Don't mark a node as superseded by itself if same doc.
                if older_doc_code == latest_doc_code:
                    continue
                # An older node might amend multiple sections; if one of those
                # is still the latest authority somewhere, we still mark this
                # node — the retrieval demotion is per-node, and the node's
                # primary purpose for this section has been overtaken.
                nodes_to_mark[older_node_id] = {
                    "status": "superseded",
                    "superseded_by": latest_doc_code,
                    "for_parent_section": sec,
                }

        self.stdout.write(
            f"\nSections amended by 2+ documents: "
            f"{sum(1 for t in section_touches.values() if len(t) >= 2)}"
        )
        self.stdout.write(
            f"Amendment nodes to mark as superseded: {len(nodes_to_mark)}"
        )

        if not nodes_to_mark:
            self.stdout.write(self.style.SUCCESS("Nothing to update."))
            return

        # Sample what we'd mark.
        sample = list(nodes_to_mark.items())[:6]
        self.stdout.write("\nSample of changes:")
        for nid, sup in sample:
            self.stdout.write(
                f"  {nid}  → superseded_by {sup['superseded_by']} "
                f"(for parent section {sup['for_parent_section']})"
            )

        if dry_run:
            self.stdout.write(self.style.WARNING("\n--dry-run: no DB writes."))
            return

        # Step 4 — apply.
        with transaction.atomic():
            for node_id, supersession in nodes_to_mark.items():
                Node.objects.filter(
                    node_id=node_id, version__is_current=True,
                ).update(supersession=supersession)

        self.stdout.write(self.style.SUCCESS(
            f"\nMarked {len(nodes_to_mark)} nodes as superseded."
        ))
