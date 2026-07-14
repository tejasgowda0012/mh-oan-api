# Voice Handoff: Structured Farmer Profile

**Status:** Implementation contract  
**Contract version:** 1  
**Chat reference:** `app/services/profile.py`  
**Audience:** Voice service developers

## 1. Objective

Implement the structured farmer profile in the voice service so chat and voice read
and update the same farmer record in Qdrant. This profile is separate from mem0
episodic memory. It stores durable facts such as the farmer's location, crops, land,
irrigation, language, and preferences.

The voice implementation may use a different agent framework, but it must use the
same identity algorithm, collection, point ID, payload schema, and merge behavior.
Do not introduce a voice-specific profile collection or field names.

## 2. What belongs in the structured profile

Store facts that are both farmer-specific and expected to remain useful across
sessions:

- name, village, district, and state;
- preferred mandi;
- crops, varieties, crop area, sowing date, and season;
- total land area, irrigation method, and soil type;
- livestock;
- preferred language and preferred call time;
- schemes the farmer is participating in;
- open follow-up topics;
- short durable notes that do not fit another structured field.

Do not store live or retrieved facts such as weather, mandi prices, scheme rules,
pesticide recommendations, document-search results, OTPs, access tokens, or entire
conversation transcripts. Unstructured past-conversation context belongs in mem0
episodic memory, not this collection.

Only promote a fact when the farmer states or confirms it. Do not infer a durable
profile fact from a one-off question. For example, "What is the cotton price?" does
not prove that the farmer grows cotton.

## 3. Shared environment

Both services must use:

```dotenv
QDRANT_HOST=<shared-qdrant-host>
QDRANT_PORT=6333
QDRANT_PROFILE_COLLECTION=vistaar_farmer_profiles
```

`QDRANT_PROFILE_COLLECTION` defaults to `vistaar_farmer_profiles` in chat. Set it
explicitly in both deployments to make cross-channel intent clear. The current
reference client assumes unencrypted HTTP and no Qdrant API key. If the shared
deployment later adds TLS or authentication, update both channels together.

The profile collection is independent of `QDRANT_COLLECTION`, which is the mem0
episodic-memory collection. The profile does not use embeddings.

## 4. Identity contract

Resolve one `memory_user_id` before reading or writing either profile or episodic
memory. Voice must copy the behavior in `app/services/identity.py`.

Resolution priority:

1. Take a phone from the JWT claim selected by `JWT_PHONE_CLAIM`, when configured.
2. Otherwise check `mobile`, `phone`, `phone_number`, `phoneNumber`, then `msisdn`.
3. If no JWT phone exists, accept a valid request phone.
4. If no phone exists, use an authenticated stable opaque JWT identifier in this
   order: `user_id`, `sub`, `farmer_id`, `farmerid`, `uid`, `id`, `unique_id`.
5. Do not create persistent memory for a guest or anonymous caller.

Normalize an Indian phone as follows:

- remove every non-digit character;
- convert ten digits to `91` plus those ten digits;
- convert an eleven-digit number beginning with `0` to `91` plus the final ten
  digits;
- reject anything that is not exactly twelve digits beginning with `91`;
- represent the normalized value as `+91XXXXXXXXXX`.

Derive the shared user key:

```text
memory_user_id = lowercase_hex(SHA-256(UTF-8(normalized_phone)))
```

Never store the raw phone in Qdrant, use it as a point ID, include it in model
context, or log it as the memory user ID.

## 5. Qdrant collection and deterministic point ID

The collection has one point per farmer and uses a placeholder vector because the
profile is retrieved directly by ID, not by semantic search.

Create the collection, if absent, with:

```json
{
  "vectors": {
    "size": 1,
    "distance": "Dot"
  }
}
```

Use this exact UUID namespace:

```text
6f9619ff-8b86-d011-b42d-00c04fc964ff
```

Derive the point ID exactly as:

```text
point_id = UUIDv5(namespace, memory_user_id)
```

The Qdrant point is:

```json
{
  "id": "<UUIDv5 point_id>",
  "vector": [0.0],
  "payload": "<FarmerProfile object below>"
}
```

Do not generate a random UUID. A random or differently namespaced UUID would create
a second profile for the same farmer and break chat/voice interoperability.

Use this non-production compatibility test vector in both implementations:

```text
input phone:    9000000001
normalized:     +919000000001
memory_user_id: 4b6b245fada6673c7625fae4deec5df737ad2eee0be28550f86fbc976a147357
point_id:       3860646d-16bd-5d30-8390-a7e795268fab
```

If voice produces different identifiers for this input, stop the rollout because it
will not share profiles with chat.

## 6. Canonical payload schema

Use the following field names and types without channel-specific additions:

```json
{
  "user_id": "<memory_user_id>",
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
  "open_threads": [
    {
      "topic": "check cotton wilt after seven days",
      "advice_given": null,
      "created_at": "2026-07-14T10:30:00+00:00",
      "status": "open"
    }
  ],
  "notes": [],
  "created_at": "2026-07-14T10:30:00+00:00",
  "updated_at": "2026-07-14T10:30:00+00:00"
}
```

All timestamps are ISO 8601 UTC strings. `user_id` in the payload must equal the
resolved `memory_user_id` used to derive the point ID.

## 7. Update and merge behavior

An agent update is a partial object. Read the existing profile, merge the partial
object, set `updated_at`, then upsert the complete payload at the deterministic point
ID.

Apply these rules:

### Scalar fields

The following fields use last non-empty explicit value wins:

```text
name, village, district, state, preferred_mandi, land_area_acres,
irrigation, soil_type, language, preferred_call_time
```

Ignore `null`, an empty string, or an empty list. Do not erase an existing value
merely because a field is absent from the partial update.

### List fields

Append new `livestock`, `schemes`, and `notes` values. Deduplicate by trimmed,
case-insensitive text while preserving the stored display value.

### Crops

Merge crops by trimmed, case-insensitive crop name. For an existing crop, replace
only the supplied non-empty subfields. Preserve its other fields. Append a crop when
its normalized name is new. Ignore a crop with no name.

### Open threads

Append an open thread only when its trimmed, case-insensitive topic is new. A newly
created thread defaults to `status="open"` and gets a UTC `created_at` timestamp.

### Creation timestamps

Create both timestamps when the profile is first created. Preserve `created_at` on
later updates and refresh only `updated_at`.

## 8. Voice read flow

At the beginning of an authenticated voice turn or call:

1. Resolve `memory_user_id` using the shared identity contract.
2. If it is absent, skip profile persistence and continue as a guest.
3. Compute the UUIDv5 point ID.
4. Retrieve that exact point with its payload.
5. Validate the payload against the canonical schema.
6. Provide only the relevant profile facts to the voice agent as trusted saved
   context.

The structured profile is authoritative for saved farm setup and location, but it
does not replace live data retrieval. A saved village may be used to request current
weather; saved weather must never be treated as current.

Keep the spoken prompt short. A suitable internal snapshot is:

```text
Saved farmer profile:
- Name: Ramesh
- Location: Bhadgaon, Jalgaon, Maharashtra
- Crop: cotton (2 acres)
- Irrigation: drip
- Preferred language: Marathi
```

Do not read internal IDs, hashes, timestamps, null fields, or implementation details
aloud.

## 9. Voice write flow

Expose a model-callable operation equivalent to:

```text
update_farmer_profile(field, value)
```

The current chat tool accepts these field labels:

```text
name, village, district, state, preferred_mandi, crop,
land_area_acres, irrigation, soil_type, livestock, language,
scheme, note
```

Map the singular tool labels as follows:

```text
crop      -> crops: [{name: value}]
livestock -> livestock: [value]
scheme    -> schemes: [value]
note      -> notes: [value]
```

Parse `land_area_acres` as a floating-point number after retaining digits and a
decimal point. The voice layer should normalize number words before this step when
needed. If parsing is uncertain, ask the farmer to clarify instead of inventing a
number.

Call the operation only after an explicit farmer statement or confirmation. Example:

```text
Farmer: "I grow cotton on two acres and use drip irrigation."
Voice operations:
1. update crop = cotton
2. update land_area_acres = 2
3. update irrigation = drip
```

Do not announce internal storage actions unless a natural confirmation helps the
conversation. Never expose `memory_user_id` or the Qdrant point ID.

## 10. Read-modify-write and concurrency

The chat v1 implementation performs read, merge, and full-point upsert. A voice
implementation must preserve the same merge semantics. Because chat and voice can
update the same farmer concurrently, serialize profile updates per
`memory_user_id`, or add a shared optimistic-concurrency strategy before allowing
high-volume simultaneous writes. A blind partial payload upsert is forbidden because
it can erase fields written by the other channel.

Any concurrency improvement must be adopted by both channels without changing the
canonical payload or deterministic ID.

## 11. Failure behavior and privacy

- Treat Qdrant failures as unavailable profile context; do not fabricate saved
  values.
- Do not let a profile-store outage block the main voice response.
- Retry connection initialization with bounded backoff and log the failure without
  raw phone numbers or profile payloads.
- Never access a profile using an ID supplied by the model. The application resolves
  identity and injects it into the operation context.
- Do not persist guests.
- Do not expose profile lookup or deletion endpoints publicly without authentication.
- Profile deletion must target only the deterministic point belonging to the current
  resolved farmer or an explicitly authorized administrative request.

## 12. Framework-neutral reference pseudocode

```python
PROFILE_NAMESPACE = UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")

def profile_point_id(memory_user_id: str) -> str:
    return str(uuid5(PROFILE_NAMESPACE, memory_user_id))

async def get_profile(memory_user_id: str):
    records = qdrant.retrieve(
        collection_name=QDRANT_PROFILE_COLLECTION,
        ids=[profile_point_id(memory_user_id)],
        with_payload=True,
    )
    return validate_farmer_profile(records[0].payload) if records else None

async def apply_profile_update(memory_user_id: str, partial: dict):
    async with per_user_lock(memory_user_id):
        existing = await get_profile(memory_user_id)
        profile = merge_profile(existing or new_profile(memory_user_id), partial)
        profile.updated_at = utc_now_iso()
        qdrant.upsert(
            collection_name=QDRANT_PROFILE_COLLECTION,
            points=[{
                "id": profile_point_id(memory_user_id),
                "vector": [0.0],
                "payload": profile,
            }],
        )
        return profile
```

## 13. Cross-channel acceptance tests

The handoff is complete only when all of these pass against the shared dev Qdrant:

1. Chat creates a profile; voice reads the same payload.
2. Voice creates a profile; chat reads the same payload.
3. `9876543210`, `09876543210`, `+91 98765 43210`, and `919876543210` resolve to
   the same `memory_user_id` and point ID.
4. A guest call creates no Qdrant point.
5. Chat saves village; voice adds a crop; the final profile contains both.
6. Voice updates the same crop with acreage; the crop is merged, not duplicated.
7. Repeating livestock or scheme values with different casing does not duplicate
   them.
8. Voice and chat never write to the mem0 episodic collection for structured fields.
9. A farmer cannot read or update another farmer's profile.
10. Qdrant downtime does not prevent the voice service from answering without saved
    context.
11. Logs and spoken responses contain neither raw phone numbers nor internal IDs.
12. A simultaneous chat/voice update does not lose either channel's unrelated field.

## 14. Source-of-truth files

- `app/services/identity.py`: identity normalization and `memory_user_id` resolution.
- `app/services/profile.py`: schema, UUID namespace, Qdrant access, and merge rules.
- `agents/tools/profile_tool.py`: chat's model-callable structured update mapping.
- `app/services/memory_context.py`: profile snapshot supplied to the agent.
- `docs/memory-layer-contract.md`: full structured plus episodic cross-channel contract.

If voice needs a schema or behavior change, update this contract and both channel
implementations together. The shared collection must never have competing schemas.
