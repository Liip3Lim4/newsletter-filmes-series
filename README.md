# 🎬 Newsletter semanal de filmes e séries

E-mail toda sexta, com visual temático:
- 🎬 **Corredor de cinema**: filmes americanos em cartaz e filmes que acabaram de chegar ao streaming (com a plataforma)
- 📼 **Videolocadora**: novas temporadas e séries novas americanas
- 🏮 **Doramas** (Coreia, Japão, China, Taiwan, Tailândia): lançamentos e continuações
- Itens sem sinopse em português ficam de fora

**Custo: R$ 0.** GitHub Actions (grátis em repositório público; em privado usa ~2 min/semana dos 2.000 min/mês grátis) + API do TMDB (grátis) + SMTP do seu e-mail.

## Configuração (≈10 min)

1. **Chave TMDB** — crie conta em https://www.themoviedb.org/signup → Settings → API → solicite uma chave (uso pessoal). Copie o **API Read Access Token**.
2. **SMTP** — opções gratuitas:
   | Provedor | SMTP_HOST | SMTP_PORT | Observação |
   |---|---|---|---|
   | Gmail | smtp.gmail.com | 465 | Requer 2FA + [senha de app](https://myaccount.google.com/apppasswords) |
   | Brevo | smtp-relay.brevo.com | 587 | 300 e-mails/dia grátis; bom para vários destinatários |
   | Outlook.com | smtp-mail.outlook.com | 587 | A Microsoft vem restringindo SMTP básico; prefira Gmail/Brevo |
3. **Repositório** — suba esta pasta para um repo no GitHub.
4. **Secrets** — em *Settings → Secrets and variables → Actions → New repository secret*:
   `TMDB_TOKEN`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` (vírgula para vários) e, opcional, `MAIL_FROM`.
5. **Teste** — aba *Actions → Newsletter semanal → Run workflow*. Marque *dry_run* para só gerar a prévia (baixe em *Artifacts*), ou desmarque para enviar.

## Texturas da estante

As imagens de madeira ficam em `assets/`. No GitHub Actions o e-mail busca essas imagens pelo CDN jsDelivr
(`cdn.jsdelivr.net/gh/<usuario>/<repo>@main/assets/`), o que **exige repositório público**. Isso não expõe nada
sensível: o token e as senhas ficam nos Secrets. Em repositório privado a estante aparece em cor sólida de madeira.
Localmente, a prévia `newsletter.html` usa os arquivos da pasta. Para hospedar em outro lugar, defina `ASSETS_URL`.
O Outlook para Windows (desktop) não exibe texturas de fundo e mostra as cores sólidas.

## Ajustes

- **Dia/hora**: edite o `cron` em `.github/workflows/newsletter.yml` (horário em UTC).
- **Quantidade por seção**: `MAX_MOVIES` (6), `MAX_SERIES` (6), `MAX_DORAMAS` (3) no bloco `env`.
- **Países**: `MAIN_COUNTRY` (US) e `DORAMA_COUNTRIES` (KR,JP,CN,TW,TH).
- **Gêneros ignorados**: `EXCLUDE_TV_GENRES` (padrão: notícias, reality, novela, talk show).
- **Teste local**:
  ```bash
  export TMDB_TOKEN=... DRY_RUN=1
  python newsletter.py   # gera newsletter.html
  ```

> ⚠️ O GitHub desativa workflows agendados em repositórios públicos sem commits por 60 dias. Se acontecer, basta reativar na aba Actions (ou fazer um commit qualquer).
