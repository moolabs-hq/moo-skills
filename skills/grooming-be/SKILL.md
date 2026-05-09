---
name: grooming-be
description: Backend LLD grooming — service/module boundaries, controller-vs-service separation, request validation, exhaustive error mapping (400/401/403/404/409/422/500 with actionable messages), DB schema/migration/index strategy, race conditions, idempotency, N+1 risks, async vs sync, observability, feature-flag evaluation, and unit/integration/manual test scenarios. STRICTLY no code. STRICTLY backend only — do not stretch to FE. Use when the user says "groom the backend", "BE implementation plan", "service-layer design", "what does the backend need to do for this story", or running BE-side grooming after HLD/contracts. Triggers on: BE grooming, backend tech spec, service design, error handling spec.
---

# Goal
* Your task is to prepare Implementation specs concentrating on BE (Backend).
* You have to think about the LLD of the task — the HLD and API contracts will already be defined by the preceding HLD tech specs step.
* As part of this process, you have to detail out how we will implement the BE requirements of this task at a service/module level.

# Supporting documents
* HLD Tech Specs document (output of the HLD step — contains schemas, API contracts, architectural decisions)
* PRD / Requirements document
* API contract (finalized in HLD step)
* Ask user to provide above documents if you do not get it.
* OR a task breakdown document in which we have to groom a given task.

# Approach

## API Implementation Details
* For every API endpoint in scope, think about:
    * Request validation — what fields are required, what are optional, what are the constraints (min/max, regex, enums)?
    * Schema consistency — request and response schemas must follow existing conventions in the codebase. Check existing DTOs/models for patterns.
    * Backward compatibility:
        * If modifying an existing API, new fields must be optional so existing clients do not break.
        * If a field is being removed or its behavior changed, document the migration path.
        * If a completely new API is being created, this concern does not apply.
    * Response shape — consistent with existing API responses in the project. Follow the established envelope/response pattern.

## Exception Handling & Error Responses
* For every API, explicitly map out the following error scenarios:
    * **400 Bad Request** — invalid input, missing required fields, constraint violations. What specific validation messages should be returned?
    * **401 Unauthorized** — authentication failures.
    * **403 Forbidden** — authorization failures. What permission checks are needed?
    * **404 Not Found** — resource does not exist. Which lookups can produce this?
    * **409 Conflict** — duplicate creation, concurrent modification conflicts.
    * **422 Unprocessable Entity** — valid syntax but semantically invalid (e.g., referencing a deleted resource).
    * **500 Internal Server Error** — unexpected failures. What should be logged? What should the client receive?
* Error responses must include actionable messages — the client should be able to determine what went wrong and what to do about it.
* Think about which errors are retryable vs terminal from the client's perspective.

## Database & Schema Changes
* If the HLD introduced schema changes:
    * Migration strategy — how will the migration be applied? Any data backfill needed?
    * Impact on existing queries — will the change affect performance of existing read patterns?
    * Index requirements — are new indexes needed for the access patterns this feature introduces?
    * Null handling — for new columns on existing tables, what is the default value? How do existing rows behave?
* If no schema changes, confirm this explicitly.

## Business Logic & Service Layer
* Where does the core business logic live? Which service/module is responsible?
* Are there existing services that should be extended vs new services that need to be created?
* Separation of concerns — controller should be thin (validation + delegation), service layer holds business logic.
* Identify shared logic that can be reused from existing services.
* Identify any orchestration needs — does this feature require coordinating multiple services/external calls?

## Edge Cases & Data Integrity
* What happens with concurrent requests? Race conditions?
* What happens if a downstream service/dependency fails mid-operation? Partial state?
* Idempotency — can the same request be safely retried?
* Data consistency — if multiple tables/resources are updated, what ensures consistency?
* Bulk operations — if applicable, what are the limits? How do partial failures get reported?

## Performance Considerations
* Identify any N+1 query risks.
* Are there operations that should be async/background rather than synchronous?
* Caching — should any read paths be cached? Cache invalidation strategy?
* Payload size — are any responses potentially large? Should pagination be enforced?
* Only flag performance concerns that are realistic given current scale. Do not suggest premature optimizations.

## Security
* Authentication — which endpoints need auth? Any public endpoints?
* Authorization — what permission/role checks are needed?
* Input sanitization — any user input that gets stored/rendered that needs sanitization?
* Sensitive data — any PII or secrets being handled? Logging considerations?
* Rate limiting — is this endpoint exposed to abuse?

## Feature Flag Evaluation
* For every feature, explicitly evaluate whether a feature flag is needed. Consider:
    * Will this feature take more than 1-2 days to complete and merge to main?
    * Is this a breaking change to an existing API that needs gradual rollout?
    * Can the feature be isolated behind a conditional path in the service layer?
* If a feature flag is needed:
    * Where is the branch point — controller level, service level, or data level?
    * Is it a global boolean flag or tenant-scoped flag?
    * What is the cleanup plan once the flag is fully rolled out?
* If the feature is a net-new API endpoint with no existing clients, a feature flag is likely unnecessary — document this reasoning.

## Observability
* What should be logged at each stage of the flow? (request received, key decisions, external calls, completion)
* Are there metrics that should be tracked? (latency, error rates, usage counts)
* For async/background operations — how do we know if something silently failed?

## From technical perspective, get inside the code and plan for:
* Reusability of existing services, utilities, and patterns.
* When services need to be extended, think about the interface changes needed. New parameters should not break existing callers.
* What new modules/services need to be created.
* Dependency injection and testability of the new code.

# Testing Strategy

## Unit Testing
* **MUST unit test** (contains business logic):
    * Service layer functions with business logic, transformations, branching
    * Validators and input sanitization logic
    * Utility/helper functions
    * Data transformation and mapping functions
    * Any function with conditional logic or computed values

* **DO NOT unit test** (no business logic):
    * Controller/route definitions that are thin wrappers delegating to services
    * ORM model definitions with no custom methods
    * Configuration files, constants, type definitions
    * Thin repository/DAO methods that just proxy to the DB client

* **Mocking policy**:
    * Prefer testing pure functions with no mocks (highest ROI)
    * For service layer tests, mock external dependencies (DB, external APIs) but not internal logic
    * If a test needs more than 3 mocks, the code under test likely needs refactoring
    * Database interactions should be tested via integration tests, not unit test mocks

* **Edge case coverage** — for every unit:
    * Happy path
    * Empty/null/missing input
    * Boundary values
    * Invalid input / type mismatches
    * Error conditions from dependencies

## Integration / API Testing
* For every API endpoint, define test scenarios covering:
    * **Happy path** — valid request, expected response, correct status code
    * **Validation failures** — missing required fields, invalid field values, constraint violations → 400
    * **Auth failures** — missing token, expired token, insufficient permissions → 401/403
    * **Not found** — valid request but resource does not exist → 404
    * **Conflict / duplicate** — if applicable → 409
    * **Server errors** — simulated downstream failure → appropriate error response
* Response schema validation — does the response match the contract?
* Side effects verification — was the DB updated correctly? Were events published?

## Manual / E2E Testing Scenarios
* For scenarios that cannot be covered by unit or integration tests, explicitly list manual test scenarios:
    * End-to-end flows that span multiple services or require specific environment state
    * Async/background job completion verification
    * Feature flag behavior — test with flag on and off
    * Backward compatibility — existing clients continue to work after the change
    * Data migration verification — if migrations were run, verify data integrity
    * Concurrent request handling — if relevant, test race conditions manually
* Each manual test scenario should have:
    * Preconditions (what state needs to exist)
    * Steps to execute
    * Expected outcome

# Documentation Strategy
* Dont be unnecessarily verbose. While being clear, do not add too much information in the documentation which is not needed, or goes beyond scope.
* [STRICT] Code can/should not be written/suggested in this task. The aim is to have good Backend implementation details based on task and code understanding, to be able to implement later. This means that coding best practices and guidelines need not be considered as of now.
* While detailed code is not required at this stage, service-level decisions, module boundaries, and LLD should be added to the documentation.
* If you are detailing this project or task or story into granular executable tasks/sub-tasks, do not detail out the code or testing strategies of the task/sub-task as of yet.
* [STRICT] Limit yourself to BE grooming only as part of this task. Do not stretch to suggest FE implementation details here.
* Derive clarity on any unclear tech implementation discussion.
* Based on all of this you are supposed to divide the BE story into manageable tasks.
* You should also think about the testing strategy (unit + integration + manual) in the grooming doc based on the testing sections above.
