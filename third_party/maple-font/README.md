# Maple Mono CN

- Product font authority: https://github.com/DaydreamZXG/maple-font
- Binary basis: upstream v7.9 `MapleMono-CN-unhinted` (the preferred fork has no binary release)
- License: SIL Open Font License 1.1 (see `OFL.txt`)

## Selected weights

The vendored source TTF files live in `third_party/maple-font/fonts/`. The WeChat product uses only:

- MapleMono-CN-Regular.ttf
- MapleMono-CN-Bold.ttf

Medium, Nerd Font, italics, and the remaining weights are intentionally excluded.

Pinned source SHA-256:

- Regular: `0fcc5139bfd5ab14ff989bb7dfd5236d0cafc10e60b9828569e6883613ab4085`
- Bold: `ca79870485fb382e01f4e70edb08bccd3cf1dbdf74b82023fdf2f0c95db55725`

## WeChat assets

Do not download or commit the full release archive. Build the two WOFF deployment assets from
the vendored TTF sources:

```bash
python3 -m pip install -r scripts/requirements-fonts.txt
python3 scripts/build_maple_cn_web_fonts.py --output-dir /tmp/maple-cn-web
```

The output names are stable because they are part of the production environment contract:

- `maple-mono-cn-regular-v7.9.woff`
- `maple-mono-cn-bold-v7.9.woff`

Pinned deployment SHA-256:

- Regular: `fd4a84f153836e20742a02f44611b29f006fce1864efd7bf43522f1b4e1f07fa`
- Bold: `64bc17d6e159d3265f55c38cfbb8267b771391d6808416595a9a713bb6f84b1b`
