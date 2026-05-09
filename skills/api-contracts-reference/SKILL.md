---
name: api-contracts-reference
description: Reference for the Moolabs node-config API contracts driving WorkflowBlockTypeDto behavior — when connectionAppId is null (FE handles conditional visibility) versus non-null (BE serves /node/updated_config_and_status with settingsSchema, settingFieldValues, nodeDefinitionId, fieldNameChanged, nodeId; returns nodeDefinition + settingFieldValues + availableOptions). Covers first-time-open vs field-change request/response shapes and which side owns the saved state. Use when implementing or reviewing node-definition flow, dynamic-form rendering, field-options calls, or anything touching WorkflowBlockTypeDto.
---

# Node Config API Contracts

* WorkflowBlockTypeDto = Node Defintion nomenclature wise.

# When WorkflowBlockTypeDto.connectionAppId is  null

* The rendering details which just includes conditional visibility tracking will be done in FE directly without invilving BE for nodes where WorkflowBlockTypeDto.connectionAppId is null.
* Harsh to chalk out details.

# When WorkflowBlockTypeDto.connectionAppId is not null

* If WorkflowBlockTypeDto.connectionAppId is not null, its an API node where the definition and options update has to happen in BE and FE will just render the same.
* The idea here is that first time node open and any field change is not fundamentally different.

## First Time Node open

### Request

```typescript
POST /node/updated_config_and_status

{
    settingsSchema: [...],        // Current node definition
    settingFieldValues: [...],     // Current field values
    nodeDefinitionId: "...",      // Node def ID
    fieldNameChanged: null,       // No field changes in first load.
    nodeId: "..."                 // Node instance ID
}
```

### Response

```typescript
{
    nodeDefinition: {
        fields: [...]            // Updated field definitions
    },
    settingFieldValues: [...] ,   // Updated setting field values.
    availableOptions: [{}]
}
```

## When any field value changes

### Request

```typescript
POST /nodes/updated-config-and-status

{
    settingsSchema: [...],        // Current node definition
    settingFieldValues: [...],     // Current field values
    nodeDefinitionId: "...",      // Node def ID
    fieldNameChanged: "field_a",  // Field which changed
    nodeId: "..."                 // Node instance ID
}
```

### Response

```typescript
{
    nodeId: "..." 
    nodeDefinition: {
        fields: [...]            // Updated field definitions
    },
    availableOptions: [
        {
            fieldName: "field_b",
            options: [...],      // New options
            context: null,
            errors:[],
            search:null
        }
    ],
    settingFieldValues: [...]    // Updated setting field values. The fields whose values needs to be cleared will come cleared in this updated setting field values.
}
```

## BE

* Based on the definition returned by reload props, we need to delete the fields in settingFieldValues which are not coming in the updated reload props node definition
  * BE will only keep the updated definition and status stateless and final saving has to be decided by FE. Current approaches will continue.
  * The logic for which field values need to be cleared, deleted or retained will be **separate shared.**

## FE

* FE will get the updated node definition and updated settingFieldValues and update in the local store. No calls to BE for saving the node yet.
  * For the availableOptions, the options rendered needs to be updated by FE.
* Note - FE need to continue to make field-options call whenever user clicks on the field, searches, pginates on the field etc. These requirements will continue to be served by FE as it is being done currently
