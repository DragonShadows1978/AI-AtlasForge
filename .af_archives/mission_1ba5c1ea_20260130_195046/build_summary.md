# Investigation Persistence - Build Summary

## Changes Made

### 1. investigation_engine.py
Added three new functions to handle investigation persistence and deletion:

- `archive_current_investigation()` (line ~340): Moves completed/failed investigations from `current` to `history` array. Called automatically at the end of `InvestigationRunner.run()`.

- `delete_investigation(investigation_id, delete_files=False)` (line ~370): Deletes a single investigation from history. Optionally deletes workspace files.

- `delete_investigations_bulk(investigation_ids, delete_files=False)` (line ~430): Bulk delete multiple investigations.

Modified `InvestigationRunner.run()` to call `archive_current_investigation()` after completion or failure (for non-email investigations).

### 2. dashboard_modules/investigation.py
Added two new API endpoints:

- `DELETE /api/investigation/<investigation_id>` - Delete a single investigation
  - Query param: `delete_files=true` to also delete workspace directory

- `POST /api/investigation/bulk/delete` - Delete multiple investigations
  - Request body: `{ "ids": [...], "delete_files": false }`

### 3. dashboard_static/src/modules/investigation-history.js
Added UI functionality:

- Added delete button (trash icon) to each investigation card
- Added `deleteInvestigation(id)` function with confirmation dialog
- Added `bulkDeleteInvestigations()` function for bulk deletion

### 4. dashboard_templates/main_bundled.html
- Added "Delete" button to the bulk action bar

### 5. Frontend Bundle
- Rebuilt with `node build.js` to include the new JavaScript functions

## Testing
All functions were tested directly:
- `archive_current_investigation()` successfully moved completed investigation to history
- `delete_investigation()` successfully removed single investigation
- `delete_investigations_bulk()` successfully removed multiple investigations

## Success Criteria Met
1. ✅ Completed investigations persist in history (auto-archived on completion)
2. ✅ Starting a new investigation doesn't remove previous completed ones
3. ✅ Users can delete investigations via trash button on cards
4. ✅ Users can bulk delete via selection mode
5. ✅ The "ODL test missions" can now be deleted (and were cleaned up during testing)
6. ✅ Optional workspace file deletion supported via `delete_files` parameter
