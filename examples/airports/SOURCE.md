# Airport example provenance

Source: [OurAirports](https://ourairports.com/data/), public domain.
Pinned commit: `4f793127dcf3f6e6b4870862a4832c9dbef455e7`.
[Original airports.csv](https://raw.githubusercontent.com/davidmegginson/ourairports-data/4f793127dcf3f6e6b4870862a4832c9dbef455e7/airports.csv)

`baseline.csv` contains eight selected records and seven unchanged columns.
This is an illustrative snapshot, not a verified error-free benchmark.
`spiked.csv` deliberately changes two country codes. These modifications are
ours, not errors attributed to OurAirports. `changes.json` records the exact
changes and must not be supplied to the model. Selection and mutations are
deterministic (no random seed is needed).
