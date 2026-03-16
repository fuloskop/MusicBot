# MusicBot

YouTube destekli Discord muzik botu. Sarki/playlist calma, kuyruk yonetimi ve kanal takip (watcher) ozellikleri iceriyor.

## Ozellikler

- YouTube linki veya arama ile sarki calma
- Playlist destegi (tum sarkilari kuyruga ekler)
- Sarki kuyrugu yonetimi (siralama, atlama, duraklatma)
- Kanal takip sistemi: belirli bir kelime gecince mesaji otomatik olarak belirlenen endpoint'e POST eder
- Takip verileri kalici (`watchers.json`), bot yeniden baslatilsa bile kaybolmaz
- Her sunucu icin bagimsiz kuyruk ve takip sistemi

## Kurulum

### Gereksinimler

- Python 3.11+
- ffmpeg

### Adimlar

```bash
# Repoyu klonla
git clone https://github.com/fuloskop/MusicBot.git
cd MusicBot

# Sanal ortam olustur ve bagimliliklari kur
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# ffmpeg kur (macOS)
brew install ffmpeg

# ffmpeg kur (Ubuntu/Debian)
# sudo apt install ffmpeg

# .env dosyasini olustur
cp .env.example .env
# .env dosyasina Discord bot tokenini yaz
```

### Discord Bot Token Alma

1. https://discord.com/developers/applications adresine git
2. **New Application** tiklayip isim ver
3. Sol menuden **Bot** sekmesine gir
4. **Reset Token** tiklayip tokeni kopyala
5. **Message Content Intent** toggle'ini ac
6. Tokeni `.env` dosyasina yapistir:

```
DISCORD_TOKEN=tokenin_buraya
```

### Botu Sunucuya Davet Etme

1. Sol menuden **OAuth2** sekmesine gir
2. **Scopes** altinda `bot` isaretlere
3. **Bot Permissions** altinda su izinleri sec:
   - Send Messages
   - Connect
   - Speak
   - Read Message History
4. Olusan URL'yi tarayicida ac ve sunucunu sec

### Botu Calistirma

```bash
source venv/bin/activate
python3 bot.py
```

## Komutlar

Tum komutlar `!` prefixi ile calisir.

### Muzik Komutlari

| Komut | Alias | Aciklama |
|-------|-------|----------|
| `!gel` | - | Botu bulundugun ses kanalina cagir |
| `!cal <link/arama>` | `!p`, `!play` | YouTube linki veya arama sorgusu ile sarki cal |
| `!liste` | `!q`, `!queue` | Surada calanan sarkiyi ve kuyruktaki sarkilari goster |
| `!atla` | `!s`, `!skip` | Calanan sarkiyi atlayip siradakine gec |
| `!duraklat` | `!pause` | Calanan sarkiyi duraklat |
| `!devam` | `!resume` | Duraklatilmis sarkiyi devam ettir |
| `!dur` | `!stop` | Calmayi tamamen durdur ve kuyrugu temizle |
| `!git` | `!leave`, `!dc` | Botu ses kanalindan cikar |

### Muzik Kullanim Ornekleri

```
# YouTube linki ile calma
!cal https://www.youtube.com/watch?v=dQw4w9WgXcQ

# YouTube playlist ile calma (tum sarkilar kuyruga eklenir)
!cal https://www.youtube.com/playlist?list=PLxxxxxxx

# Arama ile calma
!cal never gonna give you up

# Kuyruktaki sarkilari gor
!liste

# Sarkiyi atla
!atla
```

### Kanal Takip Komutlari

| Komut | Alias | Aciklama |
|-------|-------|----------|
| `!takip <#kanal> <kelime> <endpoint>` | - | Belirtilen kanalda kelime gecince endpoint'e POST at |
| `!takipler` | `!watchlist` | Aktif takipleri listele |
| `!takipkaldir <id>` | `!unwatch` | ID ile takibi kaldir |

### Takip Kullanim Ornekleri

```
# #genel kanalinda "indirim" kelimesini takip et
!takip #genel indirim https://api.example.com/webhook

# #duyurular kanalinda "update" kelimesini takip et
!takip #duyurular update https://hooks.example.com/notify

# Aktif takipleri gor
!takipler

# 1 numarali takibi kaldir
!takipkaldir 1
```

### Webhook Payload Formati

Bir kelime eslestiginde endpoint'e asagidaki JSON POST edilir:

```json
{
  "guild_id": 123456789,
  "guild_name": "Sunucu Adi",
  "channel_id": 987654321,
  "channel_name": "genel",
  "author_id": 111222333,
  "author_name": "Kullanici#1234",
  "message_id": 444555666,
  "content": "Bugun buyuk indirim var!",
  "keyword": "indirim",
  "timestamp": "2026-03-16T12:00:00+00:00"
}
```

## Deploy

### Fly.io

```bash
# Fly CLI kur ve giris yap
# https://fly.io/docs/getting-started/installing-flyctl/

# Token'i ekle
fly secrets set DISCORD_TOKEN=tokenin_buraya -a app-ismi

# Deploy et
fly deploy
```

Repo icindeki `Dockerfile` ve `fly.toml` dosyalari Fly.io icin hazir.

### Lokal

Botu kendi bilgisayarinda 7/24 calistirmak icin:

```bash
# Screen veya tmux ile arka planda calistir
screen -S musicbot
source venv/bin/activate
python3 bot.py
# Ctrl+A, D ile screen'den cik
```

## Env Degiskenleri

| Degisken | Aciklama |
|----------|----------|
| `DISCORD_TOKEN` | Discord bot tokeni (zorunlu) |
