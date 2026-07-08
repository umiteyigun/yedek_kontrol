# yedek_kontrol

Yedek Kontrol client runtime + image release updater.

## New Repository Strategy

Bu repo artik iki ayrik amaca hizmet edecek sekilde duzenlenir:

- `src` akis: `core/` ve `agent/` kodlari, image build pipeline'i.
- `runtime` akis: host scriptleri, setup, updater ve compose calistirma dosyalari.

Public paylasim icin bu repodan sanitize edilmis bir snapshot uretilir.

## Quick Links

- OneDev build spec: `.onedev-buildspec.yml`
- Image release updater: `scripts/release-updater.sh`
- Public export script: `scripts/export-public-snapshot.sh`
- Repo split dokumani: `docs/repo-split-and-public-plan.md`
