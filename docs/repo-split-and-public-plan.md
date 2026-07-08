# Repo Split ve Public Plan

Bu dokuman, operasyonel repo ve public repo ayrimini standartlastirir.

## 1) Hedef Ayrim

### A. `yedek_kontrol` (private, runtime + deploy)

- `setup.sh`, `docker-compose.yml`, `scripts/`, `config/*.example`
- release updater timer/service
- host script kurulumu

### B. `yedek_kontrol_src` (private, sadece uygulama kaynagi)

- `core/app/**`
- `agent/agent/**`
- test/lint

### C. `yedek_kontrol_public` (public, sanitize)

- docs
- ornek config dosyalari
- token/kurum/internal adres icermeyen scriptler

## 2) Token Politikasi

- Client tarafinda yalnizca `RELEASE_READONLY_TOKEN` kullanilir.
- CI/build tarafinda write token (registry push) ayridir.
- Tokenlar asla repo dosyalarina yazilmaz; sadece OneDev secrets veya host env.

## 3) OneDev Ortak Secret Kullanimi

Ayni OneDev instance icindeki projelerde ortak secret adiyla kullanilabilir:

- Ornek ad: `registry-rw-token`
- Buildspec kullanimi:
  - `@secrets:registry-rw-token@`

Her proje icin ayri secret tanimlamak yerine, group/global secret policy tercih edilir.

## 4) Public Snapshot Uretimi

`scripts/export-public-snapshot.sh` ile sanitize snapshot uretilir:

- kaynak: mevcut repo
- hedef: `/tmp/yedek_kontrol_public`
- gizli dosyalar ve local config'ler dislanir
- OneDev URL, token satirlari ve dahili host referanslari temizlenir

## 5) Rollback Modeli

`release-updater.sh` akisi:

1. hedef tag'e deploy
2. health check (`/health`, watcher)
3. fail olursa onceki tag'e rollback
4. durum `config/release-state.json` icine yazilir (hub gorur)
