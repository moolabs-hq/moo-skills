---
name: grooming-task-breakdown
description: Break down a finalized requirements + tech-spec doc into independently developable tasks/sub-tasks with definition-of-done, acceptance criteria, and testing notes for each. STRICTLY no implementation details, no code, no detailing — just the breakdown. Use when the user says "break this story into tasks", "task breakdown", "split into sub-tasks", "make tickets out of this spec", "what tasks are needed for this feature". Last grooming step — runs after requirements/HLD/contracts/BE/FE grooming and produces tickets that can be picked up independently.
---

# Goal of the task

* The goal of this task is to divide an already existing requirement and tech spec document into a list of tasks/sub-tasks that can and should be independently achieved in order to complete the given task
* The goal is to define how to break down the bigger provided Tech Spec and requirements document of the project.
* Look at things from seperation of tasks and independently/semi-independently developable, achievable tasks.
* \[STRICT\] You are not to delve into Detailed technical implementation of any task. This has to be strictly a task planning exercise.

# Supporting documents and approach

* Do consider the immediate user Prompt as the most important part.
* The tech specs document containing details of every implementation detail as well as high level architectural decisions that we have taken.
* The PRD is written in Coda. Coda documentation link has to be shared with you and access has to be provided.
* You will also be provided with Figma designs.
* If required, you can go to app.nrev.ai and go to the check the current status of the project. If localhost:3000 is running, it will also contain the same app. If a specific link inside localhost or app.nrev.ai is given use that link as the starting point.
* lastly, you have to consider the state of the current codebase as the current code divisions, abstractions, domains etc also has a role to play in dividing up the tasks.

# Approach

* The tasks should be what can be defined as a Unit. i.e. it should be sufficiently independent so that some contracts between different tasks can be defined and we can go off individually on each task.
* The tasks should not be too broad as to be unmanageable in a single go.
* At the same time,the task should not be so small as to make proper separation of tasks meaningless.
* When in doubt, ask.
* For every task, you should add Defintion of Done, Acceptance criteria, Testing that should be done for completion. Basically when can we consider the task as completed.

# Documenation strategy

* This should be a small document, just the task breakdown and individual verification steps, testing steps etc. Detailing is not needed.
* Dont be unnecessarily verbose.
* \[STRICT\] Do not get into the implementation details. Do not get into code at this stage. This is just a task breakdown stage.
* \[STRICT\] No code can/should be created at this step of the process.
