# Maple Mono CN (vendored for offline / cloud sandbox)

Source: https://github.com/subframe7536/maple-font (user-preferred fork: https://github.com/DaydreamZXG/maple-font)
Release: v7.9 MapleMono-CN-unhinted
License: SIL Open Font License 1.1 (see OFL.txt)

## App-embedded weights

Copied into `apps/ios/Gongkao/Gongkao/Resources/Fonts/` for iOS:

- MapleMono-CN-Regular.ttf
- MapleMono-CN-Medium.ttf
- MapleMono-CN-Bold.ttf

Do **not** require network download in CI/sandbox. Models must use these local files.

Full zip is not kept in git (too large). Re-download if needed:

```bash
curl -L -o MapleMono-CN-unhinted.zip \
  https://github.com/subframe7536/maple-font/releases/download/v7.9/MapleMono-CN-unhinted.zip
```
