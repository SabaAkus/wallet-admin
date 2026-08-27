# Historical input data

`Task.pdf` is the original assignment. `sample_transactions.csv` is the manually
verified normalized extraction of its 12 sample rows. Source files are never modified
by the importer.

The CSV importer expects these logical columns:

- transaction ID
- player ID
- country
- operator
- type
- amount
- currency
- status
- balance after
- date/time

Headers are matched case-insensitively after spaces and punctuation are converted
to underscores.
