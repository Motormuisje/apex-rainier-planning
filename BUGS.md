# Bug Backlog

## PML12 availability above 100% cannot be edited consistently

Observed during the machine-route refactor/human testing.

- Instance: at least one planning instance.
- Machine: `PML12`.
- Symptom: availability shows as `120%`; after editing it appears to jump back to `120%`.
- Current suspicion: existing validation/domain mismatch, not introduced by the Blueprint move.

Relevant details:

- `DataLoader._load_machines` stores availability values up to `2.0` as factors, so `1.2` is kept as `120%`.
- Machine edit API currently validates availability as `0..100`.
- UI displays machine availability as percent and posts percent values to `/api/machines/update`.
- The old pre-Blueprint machine route had the same availability update logic.

Questions to decide before fixing:

- Is availability above `100%` valid business data for this client?
- If yes, should the UI/API allow editing up to `200%`?
- If no, should import normalize or clamp availability values above `100%`?
- Should OEE, availability, and overcapacity be represented as distinct concepts?

Suggested verification:

- Try editing `PML12` availability to a value below `100`, such as `90`.
- If it still jumps back to `120`, investigate persistence/reload path.
- If only values above `100` fail, adjust the validation/business rule deliberately.
