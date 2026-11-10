# endury/store

The durable tables, to and from one hidden intervals.icu note. The note
is the only durable state a browser session has: a snapshot of the
tables, rows verbatim with their ids, never a rebuild from the plan
request. The package imports only `endury.database`, `endury.intervals`
and `endury.common`.

The note's identifiers keep the project's former name, `coachie:store`
and the ```` ```coachie-state ```` fence, as do every calendar event's
external id and tag. They are what every existing account's data is
found by, so they do not change.

## The protocol

- **Load** reads the note and decodes `base64(zlib(json))` from below its
  last fence; the markdown above the fence is the athlete's memory
  document. Bodies over 900,000 characters continue in `coachie:store:2…n`.
  A note written by a newer schema loads read-only; an older one loads as
  is and saves at the current version.
- **Restore** replaces the tables in one transaction. An empty store keeps
  the schema defaults and seeds the plan id counter at the current unix
  seconds, so a first plan's external ids never collide with an older
  install's events.
- **Dirty tracking** is a counter bumped by temporary triggers on the
  durable tables, baselined after boot, so restoring never counts as a
  change to upload.
- **Save** reads the remote revision first and raises `StoreConflict` on
  a mismatch. The dirty flag stays set until a save succeeds, and the next
  call retries.
- **Chat and takes** have notes of their own, `coachie:chat` and
  `coachie:takes`, outside the revision. The takes note holds the coach's
  briefs on coming sessions and races beside its takes on activities. They
  merge by time or by activity, newest wins, so chatting on two devices interleaves instead
  of conflicting.

| Module | What it does |
| --- | --- |
| `note.py` | The wire format and the chunking. |
| `tables.py` | The generic SQL over the durable tables: dump, replace, the seed, the dirty-tracking triggers. |
| `sync.py` | `load`, `restore`, `save` and `remote_rev`. |
| `chat.py` | The last 50 conversation rows, to and from the chat note. |
| `takes.py` | The coach's saved takes and briefs, one per subject and depth, the newest 200, to and from the takes note. |

Start reading:
- [sync.py](sync.py) — load, restore and save.
- [note.py](note.py) — what the note holds and how it is split.
