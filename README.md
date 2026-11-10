# veri/ek: indeks ekleri

Bu dal yalnız veri taşır. `master`'daki sunucu kodu burada değildir.

- `<arka uç>/ek/YYYY-MM-DD.jsonl.gz`: gömülü indeksten sonra eklenen ya da düzeltilen belgeler. Bir kez yazılır, değişmez.
- `arthur-tr-hukuk-mcp/ek/kayit.json.gz`: zamanlanmış taramanın "hangi kararlar zaten var" kaydı.

Dağıtım bu dosyaları imaja alır; sunucu açılışta gömülü indeksin üstüne işler. Biçim ve araçlar:
`master` dalında `indeks_ek.py` ve README "Index updates".

This branch carries data only: index update files applied on top of the baked indexes at boot.
