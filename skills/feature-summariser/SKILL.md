---
name: feature-summariser
description: Produce or update a "future-developer" architectural summary of a feature/PR/branch — HLD + LLD plus ADR-style decision rationale. Strictly no shipped-in-which-phase or chronology. If a previous iteration's doc is provided, append the new state on top and update; otherwise derive from the code. Output goes under docs/<feature>/. Use when the user says "summarise this feature", "write the architecture doc for this PR", "feature summary", "doc this branch for posterity", "ADR for this feature", "explain what this feature does for future devs".
---

* You need to  summarise the changes done in the system in this PR or in this branch in a way so that this document can later be used by future generations for easy understanding of the feature, for better maintenance and better clarity of code,  while doing any bug fixes or enhancements.
* It can and should contain a summary of why we took that decision(ADR type) but there is no point in writing what features were shipped in what phase of the implementation. all of that info does not help the future developers of the project in understanding the current state of the system or in understanding why a particular decision was taken. Any other details like what was shipped when is not useful for future developers in the project. 
* The purpose of this document is whole and soul to provide an understanding to the developers developing on top of this feature about what this feature contained and why the decisions we took were taken.
* You have to explain the HLD and LLD without getting into the nuances of the implementation details that have been achieved here.
* You may also be provided with the previous iteration of this feature a document. If you are provided with that, you need to consider what was present in the previous iteration, then append the changes done in this branch or PR on top of it, and update the provided documentation to reflect the latest state.
* If you are not provided another document as the starting point, you have to consider the relevant code itself as the starting point.
* In either cases, you have to write this document in such a way that future iterations on this feature will be like i) read this document ii) use it for design, spec and implementation bases on enhancements iii) update this document for future usage.
* Ensure that the architectural doc is neither extremely verbose as to be meaningless, neither so summarised that is skipping relevant information.
* Add the documentation to the folder docs under relevant feature sub folder.
