#!/bin/sh
# Haftalik tazeleme: yalniz YENI kurum kararlarini indekse ekler.
#
#   sh tazele.sh                 # data/index.db uzerinde
#   INDEX_PATH=/yol/index.db sh tazele.sh
#
# Neden --only-new: `upsert` bir belgenin govdesini DEGISTIRIR ve vektorunu
# SILER. EPDK ilk seferde --fetch-text ile (1381 sn, 3.745 belge) tarandi;
# ayni komutu tekrar kosturmak tam metinleri kisa listeleme govdeleriyle ezer
# ve her vektoru yeniden hesaplatir. --only-new indeksteki ref'i indirmeden
# once atlar; metin ve vektor kalir.
#
# Vektorler: bu makinede Voyage anahtari yok. Fly'daki start.sh her acilista
# `crawl.py --embed-only` kosturur ve yalniz eksik vektorleri doldurur; yani
# yeni belgeler dagitimdan sonra kendiliginden vektorlenir. Anahtar varsa
# `--embed` eklenerek burada da doldurulabilir.
#
# Sayfa/yil sinirlari en yeni kayitlari kapsayacak kadar genis, tum arsivi
# yeniden tarayacak kadar dar tutuldu (rekabet 40 sayfa = ~400 karar,
# kvkk 6 sayfa = ~60 ozet). Kurumun yayim hizi degisirse buradan ayarlanir.
#
# Sigorta Tahkim: LATEST_KNOWN (sources/sigorta_tahkim.py) yeni sayi cikinca
# elle artirilir; 67 numarali dergi 2026-09-20'de henuz yoktu (404).
set -e
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
I="${INDEX_PATH:-data/index.db}"
EK="$*"   # ornek: sh tazele.sh --embed

python crawl.py --only-new --source kvkk           --pages 6            --index "$I" $EK
python crawl.py --only-new --source btk            --pages 3            --index "$I" $EK
python crawl.py --only-new --source bddk                                --index "$I" $EK
python crawl.py --only-new --source spk            --years "$(date +%Y)" --index "$I" $EK
python crawl.py --only-new --source sigorta_tahkim --issues 60-66       --index "$I" $EK
python crawl.py --only-new --source rekabet        --pages 40           --index "$I" $EK
python crawl.py --only-new --source epdk           --fetch-text         --index "$I" $EK
echo "TAZELEME BITTI: $I"
