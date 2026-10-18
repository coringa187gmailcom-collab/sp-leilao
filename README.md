# SP Leilões (ambiente de teste)

Site de leilão de itens usados, só para testes. Sem pagamento real.

Rodar local: `pip install -r requirements.txt && python app.py`

Variáveis: `SECRET_KEY`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `DATA_DIR` (ou volume Railway em /data).

## Segurança (v0.6)
- Limite de login: 5 erros em 15 min (por IP ou e-mail) → bloqueio de 15 min. Log em Admin › Segurança e backups.
- 2FA (TOTP) opcional para o admin, ativado pelo próprio admin em Admin › Segurança. Códigos de recuperação guardados com hash.
  Emergência: variável `ADMIN_2FA_RESET=1` no Railway desativa o 2FA do admin no boot (remova depois).
- Backup diário automático (SQLite online backup + fotos) em `$DATA_DIR/backups`, mantém os 7 últimos; download no admin.
- Cookies Secure/HttpOnly/SameSite=Lax, CSRF, HSTS, X-Frame-Options, nosniff, Referrer-Policy, validação real de imagens (≤ 8 MB), sessão de admin expira após 30 min sem uso.
