---
name: grooming-contracts
description: Tech-spec step focused exclusively on API contracts and schemas — schema-first design, BDD/DDD-aligned endpoint count, structured (not Record<string, unknown>) response types, backward-compatibility plans for changed APIs, and clear error envelopes (e.g. 400 with actionable message vs 500 generic). NFRs and code are out of scope. Use when the user says "design the API", "what's the contract for this", "API spec", "request/response schema for X", "draft the endpoint", "schema first". Comes after grooming-requirements and before grooming-be / grooming-fe.
---

# Goal

* Your task is to detail out the tech specifications/requirements of the task/user story given to you.
* Your aim is to build the Tech specs that translate product intent into precise, buildable system definitions.
* You are specifically concentrating on API contracts and Schemas
* You should also think about performance and security. However, as we are a startup, the performance and security are not the topmost prioroty. I bigger priority is the time and complexity of implementation.
* NFRs are not a requirement as of now.

# Supporting Documents

* You will be given a document containing details of the task that has to be completed.
* You will also be given a Figma design link containing the designs of the tasks to be completed.

## API contract

* You have to think Schema first.
* Look at the current APIs and current schemas in the concerning files in the nrev-ui-2 repo.
* Figure out what changes are needed in API and / or what new APIs need to be created.
* Do not go about suggesting new APIs if some changes/enhancements in an existing API will suffice.
* However, do not force fit different business logic requirements into the same APIs.
* How many APIs need to be created and maintained should always be looked at from Behaviour Driven Design, domain separation as well as Best API design guidelines
* If you are suggesting enhancements in existing API, also suggest backward compatibility plans.
* You need to think from the perspective of APIs, schemas, edge cases, performance, security
* You need to think about the technical trade-offs and technical design decisions.
* While creating the contracts, you need to consider the following things
  * The behaviour of every button needs to be thought about.
  * If there is a listing, we need to decide the default sort order
  * If there is a listing we have to prepare for pagination/infinite scrolling
  * If API fails, We should highlight the failures and error messages. We should think from the user perspective as to what structured error message and error code makes it easy for FE to show the ligible error message to user. Example - 400 with "You need to provide prompt as a mandatory field" is a better error response than 500 with "Something Went wrong
  * While deciding the API schema, unless absolutely unavoidable due to unknown structure, always prefer types and structured response over things like `Record<string, string | number>` or open dict etc

# Documenation strategy

* Dont be unnecessarily verbose. While being clear, do not add too much information in the documentation which is not needed, or goes beyond scope.
* Make the documention read like it has to be shared as a contract between FE and BE and it should have the contract changes, what exists in the APIs and do not need change as well as the user flow mermaids. You should also the justifications or requirements for the changes.
* Any other implementaiton or details around how we got there, should be skipped from this documentation.
* \[STRICT\] The documentation should be completely clear on the High level design and approach and tech specs, but should leave implementation details to be filled in later iterations.
* \[STRICT\] Do not go about adding FE and BE implementation details as of yet. Just concentrate on the contracts and tech specs and HLDs.
* \[STRICT\] No code can/should be created at this step of the process.
* You can add mermaid diagrams to show the user journey and API calls, if it helps in understanding the API flow.
