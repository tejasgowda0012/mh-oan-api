# MahaVistaar Memory Layer Contract

**Contract version:** 1

This document is the shared implementation contract for every MahaVistaar channel,
including chat and voice. A channel may use a different agent framework, but it must
use the same identity rules, collections, record shapes, and mutation safeguards.

The chat reference implementation lives in:

- `app/services/identity.py` — cross-channel farmer identity resolution.
- `app/services/memory.py` — mem0 episodic storage and owned mutations.
- `app/services/profile.py` — structured farmer profile storage.
- `app/services/memory_context.py` — structured profile preload for a new conversation.
- `agents/tools/memory_tool.py` — model-callable episodic operations.
- `agents/tools/profile_tool.py` — model-callable structured updates.

## Architecture

The memory layer has two independent tiers in Qdrant:

1. **Structured farmer profile**
   - Collection: `QDRANT_PROFILE_COLLECTION` (default `vistaar_farmer_profiles`)
   - One deterministic point per farmer.
   - Stores durable farm facts such as location, crops, acreage, irrigation, soil,
     language, schemes, and notes.
   - Structured profile values are authoritative for farm setup and location.

2. **Episodic memory (mem0)**
   - Collection: `QDRANT_COLLECTION` (default `vistaar_chat_farmer_memories`)
   - Multiple records per farmer.
   - Stores past topics, questions, open follow-ups, and other unstructured notes.
   - Each record has an opaque `id` used for targeted update and deletion.

Qdrant is an externally deployed shared service. Channels connect with
`QDRANT_HOST` and `QDRANT_PORT`; they must not create channel-specific memory
semantics or incompatible collections.

All channels must also use compatible `MEMORY_EMBED_MODEL` and
`MEMORY_EMBED_DIMS` values for the episodic collection. Changing the embedding
model or dimensions requires a coordinated collection migration; one channel must
never make that change independently.

## Identity contract

All channels must resolve exactly the same `memory_user_id` before accessing either
tier:

1. JWT phone number, normalized to `+91XXXXXXXXXX`, then SHA-256 hashed.
2. Request phone number, normalized and hashed the same way.
3. Authenticated stable opaque ID from the JWT.
4. Request opaque ID only in an allowed authenticated/development context.

Guests have no persistent memory. Raw phone numbers must never be stored in Qdrant,
logged as memory IDs, or passed as mem0 `user_id` values.

## Episodic record contract

Every episodic record returned internally to an agent uses this shape:

```json
{
  "id": "opaque-mem0-id",
  "memory": "The farmer asked about cotton wilt last week.",
  "user_id": "resolved-memory-user-id",
  "created_at": "ISO-8601 timestamp or null",
  "updated_at": "ISO-8601 timestamp or null"
}
```

`id` is internal coordination data. It may be passed between recall, edit, and delete
tools, but it should not be shown in the farmer-facing response.

Underlying mem0 metadata may include `source` and `channel` (`chat` or `voice`) for
auditability. Channel metadata must not affect ownership or prevent cross-channel
recall and mutation.

## Structured profile schema

Both chat and voice use one profile point keyed by the deterministic UUID derived
from `memory_user_id`. Its payload follows this shared shape:

```json
{
  "user_id": "resolved-memory-user-id",
  "name": null,
  "village": null,
  "district": null,
  "state": null,
  "preferred_mandi": null,
  "crops": [
    {
      "name": "cotton",
      "variety": null,
      "area_acres": 2.0,
      "sowing_date": null,
      "season": null
    }
  ],
  "land_area_acres": null,
  "irrigation": null,
  "soil_type": null,
  "livestock": [],
  "language": null,
  "preferred_call_time": null,
  "schemes": [],
  "open_threads": [],
  "notes": [],
  "created_at": "ISO-8601 timestamp",
  "updated_at": "ISO-8601 timestamp"
}
```

Scalar fields are replaced by newer explicit farmer statements. List values are
deduplicated case-insensitively, and crops are merged by normalized crop name. A
channel must not introduce a differently named field for the same concept.

## Agent operation contract

All channels must expose equivalent operations, even if their local tool names differ:

| Operation | Chat tool | Required behavior |
| --- | --- | --- |
| Recall | `recall_farmer_memory` | Search only the current `memory_user_id`; return matching text with opaque IDs internally. |
| Create | `save_farmer_memory` | Save an episodic note for the current farmer only. |
| Edit | `edit_farmer_memory` | Recall first, use the returned ID, verify ownership, then replace that one record. |
| Delete | `delete_farmer_memory` | Recall first, use the returned ID, verify ownership, then delete that one record. |
| Profile update | `update_farmer_profile` | Save durable structured facts instead of episodic notes. |
| Profile removal | `remove_farmer_profile_value` | Remove only an explicitly retracted matching structured value. |

### Edit flow

1. The farmer explicitly corrects a previously saved episodic fact.
2. Recall relevant memories and retain their opaque IDs internally.
3. If exactly one result matches, call edit with its ID and the complete replacement text.
4. If multiple results could match, ask the farmer which memory they mean.
5. Verify `record.user_id == current memory_user_id` before mutation.
6. Confirm completion without exposing the ID.

### Delete flow

1. The farmer explicitly asks to forget or delete a saved episodic fact.
2. Recall relevant memories and retain their opaque IDs internally.
3. If exactly one result matches, call delete with its ID.
4. If multiple results could match, ask the farmer which memory they mean.
5. Verify `record.user_id == current memory_user_id` before mutation.
6. Never broaden a single-memory request into bulk deletion.

An absent record and a record owned by another farmer must produce the same
"not found" outcome to avoid leaking whether another farmer's memory exists.

## Prompting rules shared by chat and voice

- Load the structured profile once at the beginning of a new conversation. Do not
  bulk-inject episodic memories into the prompt.
- Retrieve episodic memories on demand with recall when the current message contains
  a memory candidate, references past context, or requests a correction/deletion.
- Reconcile before writing: skip an equivalent memory, edit one clearly superseded
  memory, and create only when no equivalent exists.
- Save only farmer-specific, durable context—not live weather, mandi prices, scheme
  details, or retrieved documents.
- Use the structured profile for location, crops, acreage, irrigation, and similar
  fields; use episodic memory for unstructured past-chat context.
- Never guess a memory ID.
- Remove structured values only when the farmer explicitly retracts a matching value.
- Never edit or delete without an explicit farmer correction or forget request.
- Ask for clarification when more than one saved memory could be the target.
- Do not expose memory IDs in spoken or written farmer-facing responses.
- A channel must not silently create its own identity hashing, collection names, or
  record formats.

## Cross-channel compatibility checklist

Before chat or voice deploys memory changes, verify:

- Both channels resolve the same farmer to the same `memory_user_id`.
- Both channels use the same profile and episodic collection names.
- A memory created by chat can be recalled, edited, and deleted by voice.
- A memory created by voice can be recalled, edited, and deleted by chat.
- Cross-user edit/delete attempts fail as "not found."
- Guest sessions cannot persist or mutate memory.
- Targeted deletion does not remove unrelated memories or the structured profile.
