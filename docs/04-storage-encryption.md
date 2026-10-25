# Chunk 4: Simulated storage, encryption, upload and download

Status: built and tested. **370 unit tests and 40 Oracle integration tests pass** (11 of those are new in
this chunk). **Storage is SIMULATED**: objects are written to local folders. No real cloud adapter exists and
nothing here talks to AWS, Azure or Google Cloud. Prices, scores and latencies used by the selection step
are the illustrative ones from earlier chunks.

## 1. What was built

| Part | File | Role |
|---|---|---|
| Storage interface | `app/storage/base.py` | `put`, `get`, `delete`, `exists`; `StorageError`, `ObjectNotFoundError` |
| Simulated adapter | `app/storage/simulated.py` | one adapter; folders `storage_sim/<PROVIDER>/<region>/<object key>`; labelled SIMULATED |
| Encryption | `app/crypto/service.py` | AES-256-GCM, a data key per file, wrapped by a master key from the environment |
| File service | `app/files/service.py` | upload, download, delete (the logic) |
| Endpoints | `app/api/routers/files.py` | `POST /files`, `GET /files/{id}/download`, `DELETE /files/{id}?confirm=true` |
| SQL | `app/db/repositories/files.py` | all statements use bind variables |

The selection engine (chunk 3) is called **unchanged**.

## 2. Upload, in order

Authentication and authorisation (`file:write_own`, USER only) happen first, in the router.

1. Validate the file name (no `/`, `\`, control characters; at most 255 **bytes** in UTF-8, because Oracle
   counts bytes) and the size (not empty, not over `MAX_UPLOAD_BYTES`, default 50 MiB).
2. SHA-256 of the plaintext.
3. Run the selection engine for this file (security level, expected reads, retention, tier, preset).
   If no provider-region qualifies: HTTP 422 with every rejection reason; nothing is stored.
4. Encrypt (section 3).
5. Store the ciphertext with the simulated adapter.
6. Write **all** metadata in **one** transaction: the file row, the encryption record, the location, the
   selection decision with one candidate row per provider-region (rejected ones carry their reasons),
   an `UPLOAD` access-log row with the measured latency, and a `FILE_UPLOADED` audit event.

| What fails | What the code does | Verified by |
|---|---|---|
| Storing the object (step 5) | Nothing was written to the database yet, so no metadata exists. HTTP 503 | unit test and Oracle test: row counts unchanged, no object |
| Any database write (step 6) | The transaction rolls back and the stored object is **deleted** (compensating action). The caller sees the database error | unit tests (early and late failure) and an Oracle test with an injected failure: the adapter saw `put` then `delete`, no object left, no rows |
| The compensating delete itself | The original error is still returned; the orphan is logged as `ORPHANED OBJECT` with its key | unit test |

**Not built:** an automatic sweep for orphaned objects (for example after a crash between steps 5 and 6).

## 3. Encryption (real cryptography, simulated key management)

```
file data  --AES-256-GCM(data key, random 12-byte nonce, associated data = object key)--> ciphertext + 16-byte tag
data key   --AES-256-GCM(MASTER KEY, random nonce, associated data = key version)------> wrapped key (60 bytes)
```
* A **new random 256-bit data key** and nonce for every file.
* The object key is bound in as associated data, so a ciphertext copied under another name will not decrypt.
* The database stores **only**: the wrapped key, the file nonce, the algorithm, the master-key version and the
  SHA-256 of the stored ciphertext (`file_encryption`). The plaintext data key and the master key are never
  stored in the database; the storage folder holds only ciphertext.
* The master key is the `MASTER_KEY` environment variable (32 bytes, base64). The app refuses to start without it.

### What a real cloud KMS would add (none of this exists here)
* The master key would live in a **hardware security module** and never leave it. The application would send
  the wrapped data key to the KMS and receive the data key back, instead of holding the master key itself.
* **Key policies and IAM**: only named identities may use the key, with separation of duties.
* **Automatic rotation** with key versions (here a version mismatch simply refuses to decrypt; rotation is not implemented).
* An **audit trail of every use of the key** (who unwrapped which data key, and when).
* **FIPS 140 validated hardware** and multi-region key replication for availability.

Losing `MASTER_KEY` makes every stored file permanently unreadable. Back it up outside the repository.

## 4. Download

Authorisation (`file:read_own`) and an ownership check, then:
fetch the object, **verify the SHA-256 of the stored bytes** (catches tampering), **decrypt** (GCM
authenticates the data again, independently), **verify the SHA-256 of the plaintext** against the one recorded
at upload. Any failure returns an error and no data. An access-log row with the measured fetch latency, the
serving region and the outcome (`SUCCESS`, `FAILURE`, `DENIED`) is written in every case.

| Situation | Response |
|---|---|
| Not the owner | 403, an `ACCESS_DENIED` audit event and a `DENIED` access-log row |
| Unknown file | 404 |
| File was deleted | 410 |
| Metadata exists but the object is gone | 404 "the stored object is missing from the storage provider" (logged `FAILURE`) |
| Stored bytes were changed | 502 "integrity check failed ... ciphertext hash mismatch" |
| Hash rewritten as well, bytes still changed | 502 "could not be decrypted" (GCM catches it) |
| Plaintext does not match its recorded hash | 502 "does not match its recorded SHA-256" |
| Storage unavailable | 503 |

ADMIN and AUDITOR cannot read file contents (the permission table gives them metadata permissions only).

## 5. Delete

Needs `confirm=true` (otherwise 400, nothing changes). Owner only. In one transaction: status becomes `DELETED`,
the **encryption record is removed (crypto-erase)**, the location is marked `PENDING_DELETE`, a `DELETE`
access-log row and a `FILE_DELETED` audit event are written. Then the stored object is removed and the
location row dropped. If removing the object fails, the key is still gone (the bytes are unreadable), the
response says `object_removed: false`, and the location stays `PENDING_DELETE` (no retry job exists).

## 6. Setup and commands

Add to `.env` (the app will not start without `MASTER_KEY`). Generate the key and paste it into `.env`:

```powershell
.\.venv\Scripts\python.exe -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```
```
MASTER_KEY=<the generated text>
```
Optional: `MASTER_KEY_VERSION=1`, `MAX_UPLOAD_BYTES=52428800`, `STORAGE_ROOT=storage_sim` (git-ignored).

Upload takes the **raw file as the request body** and the details as query parameters (no multipart library):

```powershell
$auth = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Method Post -Headers $auth -InFile .\report.pdf -ContentType "application/octet-stream" `
    -Uri "http://127.0.0.1:8000/files?file_name=report.pdf&security_level=HIGH"
Invoke-WebRequest -Headers $auth -OutFile .\back.pdf -Uri "http://127.0.0.1:8000/files/1/download"
Invoke-RestMethod -Method Delete -Headers $auth -Uri "http://127.0.0.1:8000/files/1?confirm=true"
```
Query parameters: `file_name` and `security_level` (LOW, MEDIUM, HIGH) are required; `expected_accesses_per_month`
(default 1), `retention_months` (12), `tier` (HOT), `preset` (BALANCED) are optional.

## 7. How this was verified

| Check | Result |
|---|---|
| Unit tests (in-memory database, REAL simulated adapter on a temporary folder) | 370 pass |
| Oracle integration tests (real database, simulated storage) | 40 pass, 11 of them new: every row in `files`, `file_encryption` (60-byte wrapped key, 12-byte nonce), `storage_locations`, `selection_decisions` and candidates, `access_logs`, `audit_events`; tamper; wrong user; forced DB failure after storing; forced storage failure; oversize; delete |
| Deliberate bugs (16) | each broke at least one test: compensating delete removed, storage failure swallowed, owner check removed, plaintext stored instead of ciphertext, plaintext-hash check skipped, key kept on delete, confirm ignored, associated data dropped, failures not logged, size limit removed (service only; router and service both), hash check bypassed, wrong user gets the file, empty file allowed, unsafe name allowed, key version unchecked. Two survived the first time (size check and ciphertext-hash check, each backed by a second layer), so tests were added that pin each layer separately |
| Real HTTP end to end (uvicorn, Oracle, temp folder) | register, login, upload 20,027 bytes, the object on disk is 20,043 bytes (plaintext + 16), does not start with `%PDF` and contains no marker; download is byte-identical with matching SHA-256; flipping one stored byte gives 502; delete without confirm 400, with confirm 200; object gone; download then 410 |

Found while testing: Oracle counts `VARCHAR2(255)` in bytes, so long accented names would have been rejected by
the database only after the object was stored. The name is now checked in bytes before anything is stored.

## 8. Limitations and honest notes

* **Simulated everywhere that matters for the cloud**: storage is a local folder, "latency" is local disk time,
  the master key sits in the process environment. This proves the logic, not cloud behaviour.
* **No orphan sweep, no retries, no replication, no failover.** A crash between storing the object and the
  commit leaves an unreferenced object. A failed physical delete leaves `PENDING_DELETE` with no retry job.
* **Whole files are held in memory** (up to `MAX_UPLOAD_BYTES`, default 50 MiB); there is no streaming or
  chunked encryption. Upload is raw-body, not multipart.
* **Key rotation is not implemented.** Only the configured master-key version can decrypt.
* **`content_sha256` is a hash of the plaintext** (limitation L-1): anyone who can read that column can test
  whether a known file is stored. The upload response returns it to the owner; mitigation by HMAC is not built.
* **403 versus 404 reveals which file ids exist** (ids are sequential). Accepted for clearer errors.
* **The file type is guessed from the extension** against a short list, otherwise `OTHER`. There is no content sniffing or allowlist.
* **Selection on small files:** min-max scoring stretches any cost difference across 0 to 1, even a fraction of a
  cent. A 20 KB file with `BALANCED` weights can therefore go to a cheaper but distant region (observed: Azure
  East US) over a nearer one. Use the `PERFORMANCE` preset (or a heavier latency weight) when latency matters.
* **No rate limiting, quotas or virus scanning.**
