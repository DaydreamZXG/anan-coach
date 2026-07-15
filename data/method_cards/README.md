# Method Cards

Put reviewed method-card YAML files in this directory before importing them into
`product.method_cards`.

Import command:

```bash
python scripts/import_method_cards.py data/method_cards
```

Only `published` cards are returned by `GET /api/v1/method-cards/{id}`.
`author_note` and `validation` stay server-side for production audit.
