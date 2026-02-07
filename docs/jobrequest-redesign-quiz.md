# JobRequest Redesign PR Quiz

Test your understanding of the structured inventory and execution options implementation.

---

## Questions

### 1. What happens when you submit a job with inline inventory in sync mode (`?sync=true`)?

A. The API returns HTTP 400 with an error message
B. The inventory is written to a temp YAML file and executed synchronously
C. The inventory is silently converted to "localhost,"
D. The inventory dict is passed directly to ansible-runner

---

### 2. What happens when you submit a job with git inventory in sync mode (`?sync=true`)?

A. The inventory is cloned and executed synchronously
B. The API returns HTTP 400 requiring async mode
C. The inventory is cached from previous clones
D. The request is automatically converted to async mode

---

### 3. How does the sync mode handler resolve an inline inventory before running the playbook?

A. Converts the dict to a comma-separated host string
B. Passes the dict directly to ansible_runner.run()
C. Writes the data to a YAML file in a temp directory and passes the file path
D. Serializes the dict to JSON and passes it as an extra_var

---

### 4. Why was the inventory column changed from `String(255)` to `JSON` in the migration?

A. JSON columns are faster to query in MariaDB
B. To support storing structured inventory (inline/git) as nested objects
C. To enable full-text search on inventory contents
D. Because MariaDB deprecated String columns

---

### 5. What special handling was needed in the Alembic migration for MariaDB's JSON column?

A. MariaDB doesn't support JSON, so we used TEXT instead
B. Existing string values had to be wrapped in JSON format before the column type change
C. A `postgresql_using` clause was needed for the type cast
D. No special handling was needed

---

### 6. How are the `check` and `diff` options passed to ansible-runner differently from `tags` and `limit`?

A. They're all passed the same way as direct keyword arguments
B. `check` and `diff` go through the `cmdline` parameter, while `tags` and `limit` are direct kwargs
C. `check` and `diff` are environment variables, while `tags` and `limit` are in extra_vars
D. All options are combined into a single `cmdline` string

---

### 7. What is the purpose of the `_find_job_by_kwarg()` helper function added to the queue integration tests?

A. To improve test performance by avoiding full Redis scans
B. To find jobs when the rq worker might have created other job keys concurrently
C. To support filtering jobs by status
D. To handle Redis key expiration during tests

---

### 8. Why was `get_repository` added as a dependency override in `test_get_job_not_found`?

A. To test the repository layer in isolation
B. To prevent the DB fallback from connecting to real MariaDB when the job isn't in Redis
C. To improve test performance by avoiding database queries
D. To verify the repository is never called when Redis has the job

---

### 9. What path traversal protections exist for the `path` field in `GitPlaybookSource` and `GitInventory`?

A. Only runtime validation in the worker
B. Schema validation rejects `..` and absolute paths; worker verifies resolved path stays inside repo
C. Paths are sanitized by removing dangerous characters
D. A regex pattern allows only alphanumeric characters

---

### 10. In the `JobRequest` schema, how is the choice between `InlineInventory` and `GitInventory` determined when inventory is a dict?

A. By checking if `data` or `repo` key exists
B. Using a Pydantic discriminated union on the `type` field
C. By trying each model and using the first one that validates
D. The API endpoint manually checks the type and instantiates the correct model

---

### 11. What is the verbosity range allowed by `ExecutionOptions`, and why?

A. 0-10 to match Ansible's unlimited verbosity levels
B. 1-5 to match the number of v's in -vvvvv
C. 0-4 because 0 is normal and 1-4 maps to -v through -vvvv
D. Any positive integer, with higher numbers producing more output

---

### 12. Where is `options` stored in the database, and why wasn't a separate table used?

A. In a separate `job_options` table with a foreign key to jobs
B. As a JSON column in the jobs table, because options are always accessed with the job
C. Serialized as a string in the existing extra_vars column
D. Not stored in the database, only in Redis

---

### 13. According to the sync vs async support matrix, which combinations support sync mode?

A. All source and inventory combinations support sync mode
B. Only local playbook with string inventory supports sync mode
C. Local playbook with string or inline inventory supports sync mode
D. Any source with inline inventory supports sync mode

---

### 14. What TypedDicts were added to `schemas.py` and what are they used for?

A. Request/response types for the API endpoints
B. Database model type hints for SQLAlchemy
C. Worker-side type hints for source_config, inventory, and options dicts
D. Redis serialization format specifications

---

### 15. How does the code ensure backward compatibility for existing API callers using string inventory?

A. String inventory is converted to inline format internally
B. The inventory field accepts `str | StructuredInventory` union type with "localhost," as default
C. A separate endpoint was created for structured inventory
D. Structured inventory is only enabled with a feature flag

---

## Answers

### 1. Answer: B

**B. The inventory is written to a temp YAML file and executed synchronously** ✅

The `_handle_local_source()` function creates a `tempfile.TemporaryDirectory()`, writes the inline inventory data to `inventory.yml` using `yaml.dump()`, and passes the file path to `run_playbook()`.

**Why other options are incorrect:**

- **A. The API returns HTTP 400 with an error message** — This was the old behavior. After the sync mode enhancement, inline inventory is now supported in sync mode. Only git inventory returns HTTP 400.
- **C. The inventory is silently converted to "localhost,"** — The code explicitly handles inline inventory by writing it to a file, not by falling back to defaults.
- **D. The inventory dict is passed directly to ansible-runner** — Ansible-runner doesn't accept dict inventory directly. It needs either a string host list or a file path.

---

### 2. Answer: B

**B. The API returns HTTP 400 requiring async mode** ✅

Git inventory requires cloning a repository, which has unpredictable latency. The code checks `isinstance(request.inventory, GitInventory)` and raises `HTTPException(status_code=400, detail="Sync mode does not support git inventory. Use async mode.")`.

**Why other options are incorrect:**

- **A. The inventory is cloned and executed synchronously** — Git operations are explicitly blocked in sync mode due to unpredictable clone latency.
- **C. The inventory is cached from previous clones** — There's no caching mechanism for git inventory in sync mode.
- **D. The request is automatically converted to async mode** — The API returns an error; it doesn't silently change the execution mode.

---

### 3. Answer: C

**C. Writes the data to a YAML file in a temp directory and passes the file path** ✅

The sync handler uses `yaml.dump(request.inventory.data, f, default_flow_style=False)` to write the inventory to `inventory.yml` in a temp directory, then passes the file path to ansible-runner.

**Why other options are incorrect:**

- **A. Converts the dict to a comma-separated host string** — This would lose all the group structure, host variables, and group variables that inline inventory provides.
- **B. Passes the dict directly to ansible_runner.run()** — The ansible-runner library doesn't accept dict inventory. It needs a file path or host string.
- **D. Serializes the dict to JSON and passes it as an extra_var** — Inventory and extra_vars are separate concepts in Ansible. You can't pass inventory as a variable.

---

### 4. Answer: B

**B. To support storing structured inventory (inline/git) as nested objects** ✅

The new inventory formats are dicts with nested structure (type, data, repo, path, etc.). A String(255) column can't store this properly, and 255 chars would be too short anyway.

**Why other options are incorrect:**

- **A. JSON columns are faster to query in MariaDB** — Performance wasn't the motivation. String columns are actually simpler to query for exact matches.
- **C. To enable full-text search on inventory contents** — Full-text search wasn't a requirement. The JSON column is for structured storage, not search optimization.
- **D. Because MariaDB deprecated String columns** — String/VARCHAR columns are not deprecated. This was a functional requirement for storing complex data.

---

### 5. Answer: B

**B. Existing string values had to be wrapped in JSON format before the column type change** ✅

MariaDB's JSON column requires valid JSON. Plain strings like `"localhost,"` would fail validation. The migration runs `UPDATE jobs SET inventory = JSON_QUOTE(inventory)` to wrap existing strings as JSON strings before changing the column type.

**Why other options are incorrect:**

- **A. MariaDB doesn't support JSON, so we used TEXT instead** — MariaDB does support JSON columns (implemented as LONGTEXT with a CHECK constraint for valid JSON).
- **C. A `postgresql_using` clause was needed for the type cast** — `postgresql_using` is PostgreSQL-specific syntax. This project uses MariaDB, so that clause was actually removed during the code review fix.
- **D. No special handling was needed** — Without the UPDATE statement, existing rows with plain string inventory values would cause the migration to fail due to JSON validation.

---

### 6. Answer: B

**B. `check` and `diff` go through the `cmdline` parameter, while `tags` and `limit` are direct kwargs** ✅

Ansible-runner doesn't have dedicated `check` or `diff` parameters, so they must be passed as `cmdline="--check --diff"`. However, `tags`, `skip_tags`, `limit`, and `verbosity` are direct parameters to `ansible_runner.run()`.

**Why other options are incorrect:**

- **A. They're all passed the same way as direct keyword arguments** — The ansible-runner library handles these options differently. Check the `run_playbook()` implementation in `runner.py`.
- **C. `check` and `diff` are environment variables, while `tags` and `limit` are in extra_vars** — None of these options are passed as environment variables or extra_vars. They're ansible-playbook CLI options.
- **D. All options are combined into a single `cmdline` string** — This would work but isn't how it's implemented. The code uses ansible-runner's native parameters where available (`tags`, `limit`, `verbosity`) and only falls back to `cmdline` for options without direct parameter support.

---

### 7. Answer: B

**B. To find jobs when the rq worker might have created other job keys concurrently** ✅

The original tests asserted `len(job_keys) == 1`, which failed when the rq worker (running for E2E tests) processed jobs from other tests. The helper finds the specific job by its `job_id` kwarg regardless of what other jobs exist.

**Why other options are incorrect:**

- **A. To improve test performance by avoiding full Redis scans** — The function still scans all `rq:job:*` keys. Performance wasn't the motivation.
- **C. To support filtering jobs by status** — The function filters by kwargs (like `job_id`), not by job status.
- **D. To handle Redis key expiration during tests** — Key expiration wasn't the issue. The race condition was about multiple jobs existing, not jobs disappearing.

---

### 8. Answer: B

**B. To prevent the DB fallback from connecting to real MariaDB when the job isn't in Redis** ✅

The `get_job` endpoint has a fallback: if `job_store.get_job()` returns None, it calls `repository.get()`. Without mocking the repository, this tries to connect to the real database and fails.

**Why other options are incorrect:**

- **A. To test the repository layer in isolation** — The test is for the API endpoint, not the repository layer. The mock is to prevent unwanted behavior.
- **C. To improve test performance by avoiding database queries** — While it does avoid DB queries, the primary reason is to prevent connection errors, not performance optimization.
- **D. To verify the repository is never called when Redis has the job** — That's tested in `test_get_job_from_redis` which asserts `mock_repo.get.assert_not_called()`. The `not_found` test is specifically about the case where the job doesn't exist anywhere.

---

### 9. Answer: B

**B. Schema validation rejects `..` and absolute paths; worker verifies resolved path stays inside repo** ✅

The Pydantic `@field_validator` rejects paths containing `..` or starting with `/`. The worker additionally uses `Path.resolve()` and `is_relative_to()` to catch symlink escapes that bypass the schema check.

**Why other options are incorrect:**

- **A. Only runtime validation in the worker** — There are two layers of protection: schema validation AND runtime validation.
- **C. Paths are sanitized by removing dangerous characters** — The validation rejects invalid paths entirely rather than sanitizing them. Sanitization could miss edge cases.
- **D. A regex pattern allows only alphanumeric characters** — Paths can contain many valid characters (slashes, dots for extensions, etc.). The validation is specifically for traversal attacks, not character restrictions.

---

### 10. Answer: B

**B. Using a Pydantic discriminated union on the `type` field** ✅

`StructuredInventory` is defined as `Annotated[Union[InlineInventory, GitInventory], Field(discriminator="type")]`. The `type` field value (`"inline"` or `"git"`) determines which model is used.

**Why other options are incorrect:**

- **A. By checking if `data` or `repo` key exists** — While those keys differ, Pydantic doesn't use key presence for discrimination by default.
- **C. By trying each model and using the first one that validates** — That's Pydantic's default union behavior, but discriminated unions are more explicit and efficient. They check the discriminator field first.
- **D. The API endpoint manually checks the type and instantiates the correct model** — Pydantic handles this automatically during request parsing. No manual type checking is needed in the route handler.

---

### 11. Answer: C

**C. 0-4 because 0 is normal and 1-4 maps to -v through -vvvv** ✅

The schema defines `Field(default=0, ge=0, le=4)`. Ansible-runner converts `verbosity=3` to `-vvv` internally. Levels beyond 4 don't add more output.

**Why other options are incorrect:**

- **A. 0-10 to match Ansible's unlimited verbosity levels** — Ansible's practical verbosity maxes out at `-vvvv` (4 v's). Higher levels don't add more output.
- **B. 1-5 to match the number of v's in -vvvvv** — The range starts at 0 (normal output, no -v flag), not 1.
- **D. Any positive integer, with higher numbers producing more output** — The schema explicitly constrains it to 0-4 with `ge=0, le=4` validators. Values like 5 or -1 are rejected.

---

### 12. Answer: B

**B. As a JSON column in the jobs table, because options are always accessed with the job** ✅

Options are stored in an `options` JSON column. Since options are always read/written with the job and never queried independently, a separate table would add complexity without benefit.

**Why other options are incorrect:**

- **A. In a separate `job_options` table with a foreign key to jobs** — Options are stored directly in the jobs table as a JSON column, not in a separate table.
- **C. Serialized as a string in the existing extra_vars column** — Options and extra_vars are separate fields with different purposes. extra_vars are passed to the playbook; options control how ansible-playbook runs.
- **D. Not stored in the database, only in Redis** — The migration adds an `options` JSON column to the jobs table. Options are persisted to DB for job history.

---

### 13. Answer: C

**C. Local playbook with string or inline inventory supports sync mode** ✅

The sync vs async support matrix shows that sync mode works when "everything is local". Local playbook + string inventory and local playbook + inline inventory both support sync. Git sources or git inventory require async mode.

**Why other options are incorrect:**

- **A. All source and inventory combinations support sync mode** — Git sources and git inventory are explicitly rejected in sync mode due to unpredictable clone latency.
- **B. Only local playbook with string inventory supports sync mode** — This was the old behavior. The sync mode enhancement added support for inline inventory too.
- **D. Any source with inline inventory supports sync mode** — Git playbook sources are rejected in sync mode regardless of inventory type.

---

### 14. Answer: C

**C. Worker-side type hints for source_config, inventory, and options dicts** ✅

`PlaybookSourceConfig`, `RoleSourceConfig`, `InlineInventoryConfig`, `GitInventoryConfig`, and `ExecutionOptionsConfig` TypedDicts provide type hints for the serialized dicts passed through the queue to the worker.

**Why other options are incorrect:**

- **A. Request/response types for the API endpoints** — API request/response types use Pydantic BaseModel classes, not TypedDicts.
- **B. Database model type hints for SQLAlchemy** — SQLAlchemy models are in `models.py` and use Mapped type annotations, not TypedDicts.
- **D. Redis serialization format specifications** — Redis uses JSON serialization. The TypedDicts are for Python type checking, not serialization format.

---

### 15. Answer: B

**B. The inventory field accepts `str | StructuredInventory` union type with "localhost," as default** ✅

The `JobRequest.inventory` field is typed as `str | StructuredInventory = "localhost,"`. Existing callers passing strings continue to work unchanged, and the default remains `"localhost,"`.

**Why other options are incorrect:**

- **A. String inventory is converted to inline format internally** — String inventory is kept as-is and passed directly to ansible-runner. No conversion happens.
- **C. A separate endpoint was created for structured inventory** — The same `/api/v1/jobs` endpoint handles both. The request body schema determines which format is used.
- **D. Structured inventory is only enabled with a feature flag** — Both formats are always available. No feature flag is needed.
