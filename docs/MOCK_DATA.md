# Mock data plan

Agreed Nov 14 before generating the seed data. Shapes follow how the real product works; **every value is invented**. Do not add real insurer names, rates, limits, commission or policy wording to this repo.

## How the product decides the sum insured

1. **The owner states a value to cover**, if they want to.
2. Otherwise the insurer's table gives a **default value** for the race kind and rating band.
3. Either way, a value **above the insurer's authority limit is referred** to an underwriter. No charge until they approve.

The agent's own valuation (from sales, earnings, breeding and rating) does not set the figure. It **checks** it: a large gap between the owner's figure and the evidence goes into the referral pack. Evidence only ever adds caution.

## Insurer rules (one versioned document, all values invented)

- **Two codes, two rates:** Flat, and jumps (hurdles, chases, bumpers). Jumps costs roughly three times as much.
- **Default value by race kind and rating band.** Flat: pattern races (Group 1 to 3, Listed) at the top, handicaps banded by official rating, then open maidens, then auction and median-sire maidens, then low-rated handicaps. Jumps: graded and Listed races at the top, handicaps by rating band, then maiden and novice hurdles and bumpers, then low-rated handicaps.
- **Minimum premium**, for low-value runners.
- **One flat insurance tax** added to the premium.
- **Two optional add-ons at fixed prices:** life-saving surgery and vet fees, each priced separately for Flat and jumps.
- **Authority limit:** above it, refer.
- **Cover period:** from leaving the training yard to returning to it, 48 hours at most. This is the "before the horse leaves the stable" deadline.
- **Pre-existing conditions are excluded.** A known condition is listed as an exclusion on the policy; a recent issue raises the price.

## Collections to seed

| Collection | Contents |
|---|---|
| `insurer_rules` | The rules above, versioned |
| `races` | One Flat card and one jumps card at an invented course, with embedded runners |
| `horse_profiles` | Runs, official rating, breeding, sales history, health records, past declarations |
| `instructions` | Owner, standing instruction, ownership share per horse, optional stated value |
| `tasks` | One per declared runner, opened by the agent |

## Traps copied from real feeds

- Numbers arrive as strings (rating, age, field size).
- `off_dt` is the real start time; `off_time` is a 12-hour display string with no am or pm.
- `race_status` runs `entry → weighed → declared → result | abandoned`. Only `declared` is insurable.
- The results feed uses `off`, `class` and `dist_f` where the race feed uses `off_time`, `race_class` and `distance_f`. This is our renamed-field demo.
- One product is spelled two ways in two systems.
- Two horses share a name; joins are always on `horse_id`.

## Demo runners

| Runner | Setup | What the judges see |
|---|---|---|
| Two prices | Same rating band, one Flat and one jumps runner | Two prices with plain-language reasons |
| Owner over-values | Owner states a value above the limit; evidence points far lower | No charge; underwriter pack shows both figures |
| Contradiction | Declared unfit two weeks ago, "fit" today | Referred |
| Pre-existing condition | Known condition in health records | Covered, with the condition excluded |
| No answer | Owner never confirms | Stops with no charge; counted as "not confirmed", not missed |
| Entry stage | Race still at `entry` | Not eligible, never charged |
| Duplicate names | Two horses, same name, different IDs | Correct horse by ID |
| Clean runners | Five or six ordinary profiles | A full meeting for the scoreboard |

Closing line of the demo: every declared runner covered, referred, not confirmed or not eligible. **Missed: 0.**
