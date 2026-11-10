# pcd-critic-provenance

Verify that every number is reachable from frozen evidence.

## Do
- Re-hash every stored payload in `data/frozen/<case>/` against `MANIFEST.json`.
- Resolve every registry source reference against `work/sources.jsonl`; every
  `filed` date must be ≤ the case cutoff.
- Check the note's sources block exists and matches the accessions its claims
  rely on.
- Check every model finding marked `requires_note_disclosure` surfaces in the
  note (limitations + footnotes block), mapped per the finding id. A break in a
  concept the note never cites owes no footnote — do not demand boilerplate.

## Write
`cases/<case>/rounds/<round>/critic/provenance.json`.

## Cannot
- edit the candidate or the snapshot;
- accept a "the number is obviously right" without a resolved source path.
