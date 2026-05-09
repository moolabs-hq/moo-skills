---
name: grooming-requirements
description: First grooming step — turn a Coda PRD plus Figma designs into an exhaustive, gap-questioned product-requirements document covering system behaviour, constraints, empty states, error scenarios, pagination, sort orders, hover states, destructive-action confirmations, and post-action navigation. STRICTLY no implementation, code, timelines, or task breakdown. Use when the user says "groom this PRD", "understand the requirements", "PRD analysis", "what should this feature do", "requirements doc", or starts a fresh PRD discovery flow. First skill in the grooming chain — followed by HLD, contracts, BE/FE grooming, and task breakdown.
---

# Goal of the task

* The goal of this task is to understand the development requirements out of  PRD completely and exhaustively.
* The goal is to define what needs to be built and how it should behave.
* Look at things from System behavior, and constraints perspectives
* The finalised requirements need not be a blind following of designs and PRDs. You have the right to question/suggest changes if you foresee constraints, trade-offs and differing design decisions. However, for any such change you have to ask/get confirmation from your user.
* \[STRICT\] You are not to delve into Detailed technical implementation, No timelines, no tasking, no task detailing.

# Supporting documents and approach

* Do consider the immediate user Prompt as the most important part.
* The PRD is written in Coda. Coda documentation link has to be shared with you and access has to be provided. Ask for it if unavailable.
* Given a coda PRD documentation, your task is to understand the requirements properly.
* You also need to go to Figma designs mentioned in the Coda documentation and checkout the related designs for the task.
* Then you need to go to app.nrev.ai and go to the check the current status of the project. If localhost:3000 is running, it will also contain the same app. If a specific link inside localhost or app.nrev.ai is given use that link as the starting point.

# What should be considered and clarified

* If the user Prompt details the requirements in crude way, or have some vagueness to it, get clarifications from the user.
* If you find gaps in the design from the below perspectives, you have to ask relevant questions/ highlight gaps for the same.
  * Missing empty states
  * Missing error scenarios when API/component fails
  * Missing paginations/infinite scrolling for listings
  * Missing default sort orders for listings
  * Missing Hover components and hover messages for hover components, if required.
  * Missing Functionality details of every clickable component on screen.
  * Missing confirmation modal for any destructive actions like delete?
  * Where do we land from these screens.
  * What params if any, are to be passed from this page to next?
  * What will be the flow at the end of the functionality/click.
* Now you should ask me all questions that are not clear by looking at the documentation.
* Using all of this your task is to understand completely all of the requirements that need to be achieved in this task/PRD.

# Documenation strategy

* Dont be unnecessarily verbose. While being clear, do not add too much information in the documentation which is not needed, or goes beyond scope.
* While writing you have to be extremely clear on the approach and what we are achieving and how. However, the detailed technical understanding of the task can be divided into further sections.
* \[STRICT\] Never assume and never start implementing before full clarity on the requirements. First ask all relevant questions and then only start writing the technical requirements.
* \[STRICT\] No code can/should be created at this step of the process.
* The final goal is to document the requirements understanding from product point of view and fill gaps in requirements and design.
