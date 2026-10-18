"""SP Leilões — plataforma de leilões online.
Flask + SQLite. Dados e fotos em DATA_DIR (volume do Railway em /data)."""
import os, io, json, time, sqlite3, secrets, threading, re, zipfile, tempfile, base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from functools import wraps
from markupsafe import Markup
from urllib.parse import urlencode, quote
from flask import (Flask, g, request, session, redirect, url_for, render_template,
                   flash, abort, jsonify, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import pyotp, segno
from PIL import Image, ImageOps
import pillow_heif
pillow_heif.register_heif_opener()   # fotos HEIC/HEIF (iPhone/Samsung)

VERSION = "1.2.1"
TZ = ZoneInfo("America/Sao_Paulo")
BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.join(BASE, "data")
UPLOADS = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "leiloes.db")
os.makedirs(UPLOADS, exist_ok=True)

EXTEND_WINDOW = 120   # lance nos últimos 2 min...
EXTEND_BY = 120       # ...estende o término em 2 min
STAGGER = 120         # lotes de um leilão encerram de 2 em 2 min
SOON = 86400          # selo "Encerrando" quando faltam < 24 h
CATEGORIAS = ["Veículos", "Motos", "Caminhões", "Sinistros/Sucatas", "Imóveis", "Materiais"]
CAT_ICON = {"Veículos": "car", "Motos": "moto", "Caminhões": "truck", "Sinistros/Sucatas": "crash",
            "Imóveis": "building", "Materiais": "monitor"}
CAT_SLUG = {"Veículos": "veiculos", "Motos": "motos", "Caminhões": "caminhoes", "Sinistros/Sucatas": "sinistros",
            "Imóveis": "imoveis", "Materiais": "materiais"}
# grupos da busca rápida / "Leilões em destaque": (slug, rótulo, ícone, categorias)
GRUPOS = [("veiculos", "Veículos", "car", ["Veículos", "Motos", "Caminhões", "Sinistros/Sucatas"]),
          ("imoveis", "Imóveis", "building", ["Imóveis"]), ("materiais", "Materiais", "monitor", ["Materiais"])]
GRUPO = {g[0]: g for g in GRUPOS}
MOD_LABEL = {"Online": "SOMENTE ONLINE", "Presencial": "PRESENCIAL", "Online e presencial": "ONLINE E PRESENCIAL"}
WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]
CAT_MAP = {}
EXTS = {"jpg", "jpeg", "png", "webp", "gif"}
OLD_COND = ("Item usado, vendido no estado em que se encontra. Confira fotos e descrição antes de dar lance.\n"
            "Ambiente de teste: não há pagamento pelo site nem comissão. Valor, forma de pagamento e prazo "
            "são combinados diretamente entre vendedor e vencedor.")
OLD_PICK = "Retirada combinada com o vendedor após o encerramento. Os contatos aparecem para vendedor e vencedor."
V3_COND = ("Item usado, vendido pela SP Leilões no estado em que se encontra. Confira fotos e descrição antes de dar lance.\n"
           "Ambiente de teste: não há pagamento pelo site nem comissão. Após o encerramento, a SP Leilões entra em contato "
           "com o vencedor para combinar pagamento e retirada.")
V3_PICK = "Retirada combinada com a SP Leilões após o encerramento, pelo contato informado no cadastro do vencedor."
DEF_COND = ("Bem usado, vendido no estado de conservação em que se encontra, sem garantia. Recomenda-se conferir fotos, "
            "descrição e ficha técnica e, quando disponível, visitar o bem antes de ofertar lances.\n"
            "O arrematante paga o valor do lance vencedor acrescido da comissão do leiloeiro indicada no lote, "
            "nas condições e prazos informados pela SP Leilões após o encerramento.")
DEF_PICK = "Retirada agendada com a SP Leilões após a confirmação do pagamento, no local e prazo informados ao arrematante."

ICONS = {
 "car": '<path d="M19 17h2c.6 0 1-.4 1-1v-3c0-.9-.7-1.7-1.5-1.9L18 10l-2.7-3.6A2 2 0 0 0 13.7 6H6.3a2 2 0 0 0-1.6.8L2 10.5V16c0 .6.4 1 1 1h2"/><circle cx="7" cy="17" r="2"/><path d="M9 17h6"/><circle cx="17" cy="17" r="2"/>',
 "phone": '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M12 18h.01"/>',
 "washer": '<rect x="3" y="2" width="18" height="20" rx="2"/><circle cx="12" cy="13" r="5"/><path d="M7 6h.01M10 6h.01"/>',
 "sofa": '<path d="M20 9V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v3"/><path d="M2 11v5a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-5a2 2 0 0 0-4 0v2H6v-2a2 2 0 0 0-4 0Z"/><path d="M4 18v2M20 18v2"/>',
 "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
 "box": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.29 7 12 12l8.71-5M12 22V12"/>',
 "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
 "heart": '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
 "gavel": '<path d="m14 13-7.5 7.5a2.12 2.12 0 0 1-3-3L11 10"/><path d="m16 16 6-6M8 8l6-6M9 7l8 8M21 11l-8-8"/>',
 "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
 "calendar": '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
 "pin": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
 "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
 "filter": '<path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/>',
 "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
 "check": '<path d="M20 6 9 17l-5-5"/>',
 "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
 "x": '<path d="M18 6 6 18M6 6l12 12"/>',
 "left": '<path d="m15 18-6-6 6-6"/>', "right": '<path d="m9 18 6-6-6-6"/>',
 "moto": '<circle cx="5.5" cy="17" r="3.5"/><circle cx="18.5" cy="17" r="3.5"/><path d="M15 6h2l3.5 11M5.5 17 9 10h6l-3 7h-3"/><path d="M8 10 7 7H4"/>',
 "truck": '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62L18.3 8.38A1 1 0 0 0 17.52 8H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
 "crash": '<path d="M5 17H3v-5l2.5-4h7l3 4H19a2 2 0 0 1 2 2v3h-2"/><circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/><path d="M9 17h6M14 3l-1 3 3-1-1 3"/>',
 "building": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
 "monitor": '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
 "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
 "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
 "userplus": '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M19 8v6M22 11h-6"/>',
 "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
 "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
 "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
 "trophy": '<path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6M18 9h1.5a2.5 2.5 0 0 0 0-5H18M4 22h16M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22M18 2H6v7a6 6 0 0 0 12 0V2Z"/>',
}
SETTINGS = [  # (chave, rótulo)
    ("empresa", "Nome da empresa"), ("cnpj", "CNPJ"), ("leiloeiro_nome", "Nome do leiloeiro oficial"),
    ("leiloeiro_matricula", "Matrícula do leiloeiro (JUCESP)"), ("whatsapp", "WhatsApp"), ("telefone", "Telefone"),
    ("email", "E-mail de atendimento"), ("endereco", "Endereço"), ("horario", "Horário de atendimento"), ("instagram", "Instagram (@usuario)"),
    ("_notas", "Notas de arrematação (valores padrão)"),
    ("razao_social", "Razão social (vazio = nome da empresa)"), ("cidade", "Cidade/UF (ex.: São Paulo/SP)"),
    ("pix_chave", "Chave PIX (CNPJ/CPF, e-mail, telefone +55DDDnúmero ou chave aleatória)"), ("favorecido", "Favorecido / titular da conta (vazio = razão social)"),
    ("banco", "Banco (ex.: 341 — Itaú Unibanco)"), ("agencia_conta", "Agência / Conta (ex.: 0000 / 00000-0)"),
    ("retirada_local", "Local de retirada dos bens"), ("retirada_horarios", "Horários de retirada"),
    ("comissao_padrao", "Comissão padrão do leiloeiro (%) — vale quando o lote não define"), ("taxa_adm_padrao", "Taxa administrativa padrão (R$)"),
    ("taxa_patio_dia", "Taxa de pátio por dia (R$)"), ("prazo_pagamento", "Prazo de pagamento (ex.: 48 horas úteis após o leilão)"),
    ("prazo_retirada", "Prazo de retirada (ex.: 5 dias úteis após a confirmação do pagamento)"), ("multa_pct", "Multa por não pagamento (% do lance)"),
]

def icon(name, size=20, cls=""):
    return Markup(f'<svg class="ic {cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS.get(name, "")}</svg>')

BACKUPS = os.path.join(DATA_DIR, "backups")
os.makedirs(BACKUPS, exist_ok=True)
ON_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT_ID") or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("COOKIE_SECURE"))
MAX_FAILS, FAIL_WINDOW, LOCK_SECS = 5, 15 * 60, 15 * 60
ADMIN_IDLE = 30 * 60          # sessão do admin expira após 30 min sem uso
MAX_PHOTO = 20 * 1024 * 1024  # até 20 MB por foto ANTES da compressão
MAX_PHOTOS = 10                # fotos por lote
PHOTO_SIDE = 1600              # maior lado após redimensionar
Image.MAX_IMAGE_PIXELS = 120_000_000
KEEP_BACKUPS = 7

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config.update(SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
                  MAX_CONTENT_LENGTH=MAX_PHOTOS * MAX_PHOTO + 5 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SECURE=ON_RAILWAY,
                  PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
  pw_hash TEXT NOT NULL, phone TEXT DEFAULT '', is_admin INTEGER DEFAULT 0, created_at INTEGER);
CREATE TABLE IF NOT EXISTS lots(id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL, title TEXT NOT NULL,
  description TEXT DEFAULT '', category TEXT, start_cents INTEGER NOT NULL, inc_cents INTEGER NOT NULL,
  end_ts INTEGER NOT NULL, created_at INTEGER, status TEXT DEFAULT 'open', winner_id INTEGER,
  final_cents INTEGER, closed_at INTEGER, is_example INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL, filename TEXT NOT NULL, pos INTEGER);
CREATE TABLE IF NOT EXISTS bids(id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL, created_at INTEGER, extended INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS auctions(id INTEGER PRIMARY KEY, seller_id INTEGER NOT NULL, title TEXT NOT NULL,
  description TEXT DEFAULT '', location TEXT DEFAULT '', end_ts INTEGER NOT NULL, created_at INTEGER, is_example INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL, lot_id INTEGER NOT NULL, created_at INTEGER, PRIMARY KEY(user_id, lot_id));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS login_log(id INTEGER PRIMARY KEY, ts INTEGER, ip TEXT, email TEXT, kind TEXT, until_ts INTEGER);
CREATE INDEX IF NOT EXISTS ix_login_log ON login_log(kind, ts);
CREATE TABLE IF NOT EXISTS email_tokens(id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, expires_ts INTEGER, created_at INTEGER, used_at INTEGER);
CREATE TABLE IF NOT EXISTS email_log(id INTEGER PRIMARY KEY, ts INTEGER, to_email TEXT, kind TEXT, ok INTEGER, info TEXT);
CREATE TABLE IF NOT EXISTS habilitations(user_id INTEGER NOT NULL, auction_id INTEGER NOT NULL, created_at INTEGER, PRIMARY KEY(user_id, auction_id));
CREATE TABLE IF NOT EXISTS banners(id INTEGER PRIMARY KEY, filename TEXT NOT NULL, link TEXT DEFAULT '', title TEXT DEFAULT '',
  pos INTEGER DEFAULT 0, active INTEGER DEFAULT 1, created_at INTEGER);
CREATE TABLE IF NOT EXISTS pw_tokens(id INTEGER PRIMARY KEY, user_id INTEGER, token_hash TEXT UNIQUE, expires_ts INTEGER, created_at INTEGER, used_at INTEGER);
CREATE TABLE IF NOT EXISTS notas(id INTEGER PRIMARY KEY, lot_id INTEGER NOT NULL, user_id INTEGER NOT NULL, year INTEGER NOT NULL, seq INTEGER NOT NULL,
  status TEXT DEFAULT 'Pendente', data TEXT NOT NULL, issued_at INTEGER, updated_at INTEGER, sent_at INTEGER, sent_count INTEGER DEFAULT 0,
  UNIQUE(lot_id, user_id), UNIQUE(year, seq));
CREATE INDEX IF NOT EXISTS ix_bids_lot ON bids(lot_id, amount_cents);
CREATE INDEX IF NOT EXISTS ix_lots_status ON lots(status, end_ts);
"""
USER_COLS = {"email_verified": "INTEGER DEFAULT 0", "email_verified_at": "INTEGER", "email_verified_by": "TEXT", "verify_sent_at": "INTEGER",
             "cpf": "TEXT", "birth": "TEXT", "cep": "TEXT", "street": "TEXT", "number": "TEXT", "complement": "TEXT",
             "district": "TEXT", "city": "TEXT", "uf": "TEXT", "profile_at": "INTEGER",
             "rg": "TEXT", "totp_secret": "TEXT", "totp_enabled": "INTEGER DEFAULT 0", "totp_last": "INTEGER DEFAULT 0", "recovery_hashes": "TEXT DEFAULT '[]'"}
LOT_COLS = {"auction_id": "INTEGER", "lot_number": "INTEGER", "conditions": "TEXT DEFAULT ''", "pickup": "TEXT DEFAULT ''",
            "views": "INTEGER DEFAULT 0", "specs": "TEXT DEFAULT ''", "appraisal_cents": "INTEGER", "commission_pct": "REAL DEFAULT 5", "fees": "TEXT DEFAULT ''", "tags": "TEXT DEFAULT ''", "note": "TEXT DEFAULT ''"}
AUC_COLS = {"start_ts": "INTEGER", "modality": "TEXT DEFAULT 'Online'", "edital": "TEXT DEFAULT ''", "ribbon": "TEXT DEFAULT ''"}
MODALIDADES = ["Online", "Presencial", "Online e presencial"]
V4_FEES = "Sem taxas administrativas (ambiente de teste)."
DEF_FEES = "Consulte as condições de venda e o edital do leilão."
V4_EDITAL = """1. O leilão é realizado pela SP Leilões em ambiente de TESTE, sem pagamento real pelo site.
2. Para dar lances é preciso estar cadastrado e habilitado neste leilão (aceite deste edital).
3. Os bens são usados e vendidos no estado em que se encontram. Fotos e descrições são ilustrativas; a visitação pode ser combinada antes do encerramento.
4. Lance mínimo = lance atual + incremento do lote. Lances são irrevogáveis.
5. Os lotes encerram em sequência a partir do horário indicado. Lance nos últimos 2 minutos prorroga o lote em 2 minutos.
6. Vence o maior lance ao final do cronômetro. Sobre o valor arrematado incide a comissão do leiloeiro indicada no lote.
7. Após o encerramento, a SP Leilões entra em contato com o arrematante para pagamento e retirada."""
DEF_EDITAL = """1. DO LEILÃO: o leilão é realizado na modalidade e nas datas indicadas nesta página, por meio da plataforma SP Leilões.
2. DA PARTICIPAÇÃO: podem participar pessoas maiores de 18 anos, cadastradas e habilitadas neste leilão. A habilitação implica a aceitação integral deste edital.
3. DOS BENS: os bens são usados e vendidos no estado de conservação em que se encontram, sem garantia. Fotos e descrições são meramente ilustrativas; é responsabilidade do interessado examinar o bem antes de ofertar lances.
4. DOS LANCES: o lance mínimo corresponde ao lance atual acrescido do incremento do lote. Os lances são irrevogáveis e irretratáveis.
5. DO ENCERRAMENTO: os lotes encerram em sequência a partir do horário indicado. Lance ofertado nos últimos 2 minutos prorroga o encerramento do lote em 2 minutos.
6. DA ARREMATAÇÃO: será considerado arrematante quem ofertar o maior lance ao final do cronômetro. Sobre o valor arrematado incide a comissão do leiloeiro indicada no lote.
7. DO PAGAMENTO E DA RETIRADA: após o encerramento, a SP Leilões entra em contato com o arrematante para informar a forma e o prazo de pagamento e agendar a retirada. O não pagamento no prazo poderá acarretar o cancelamento da arrematação.
8. DISPOSIÇÕES GERAIS: casos omissos serão resolvidos pela SP Leilões, com base na legislação aplicável."""

LOTQ = """SELECT * FROM (SELECT l.*, a.title auction_title, a.start_ts auction_start, a.modality auction_mod, a.end_ts auction_end,
  (SELECT MAX(amount_cents) FROM bids b WHERE b.lot_id=l.id) cur,
  (SELECT COUNT(*) FROM bids b WHERE b.lot_id=l.id) nb,
  (SELECT user_id FROM bids b WHERE b.lot_id=l.id ORDER BY amount_cents DESC, id ASC LIMIT 1) leader_id,
  (SELECT filename FROM photos p WHERE p.lot_id=l.id ORDER BY pos LIMIT 1) photo,
  COALESCE((SELECT MAX(amount_cents) FROM bids b WHERE b.lot_id=l.id), l.start_cents) price
  FROM lots l LEFT JOIN auctions a ON a.id=l.auction_id) x"""
AUCQ = """SELECT * FROM (SELECT a.*, (SELECT COUNT(*) FROM lots WHERE auction_id=a.id) nlots,
  (SELECT COUNT(*) FROM lots WHERE auction_id=a.id AND status='open') nopen,
  (SELECT MIN(end_ts) FROM lots WHERE auction_id=a.id AND status='open') next_end,
  (SELECT COUNT(*) FROM bids b JOIN lots l ON l.id=b.lot_id WHERE l.auction_id=a.id) nbids,
  (SELECT COUNT(*) FROM habilitations h WHERE h.auction_id=a.id) nhab,
  (SELECT COALESCE(SUM(views),0) FROM lots WHERE auction_id=a.id) nviews,
  (SELECT p.filename FROM photos p JOIN lots l ON l.id=p.lot_id WHERE l.auction_id=a.id ORDER BY l.lot_number, p.pos LIMIT 1) cover
  FROM auctions a) y"""

def connect():
    c = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA foreign_keys=ON")
    return c

def db():
    if "db" not in g:
        g.db = connect()
    return g.db

@app.teardown_appcontext
def _close(_):
    c = g.pop("db", None)
    if c: c.close()

# ---------- helpers ----------
def now(): return int(time.time())

def brl(cents):
    if cents is None: return "-"
    s = f"{cents/100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")

def parse_money(s):
    s = (s or "").replace("R$", "").replace(" ", "").strip()
    if not s: return None
    if "," in s: s = s.replace(".", "").replace(",", ".")
    try:
        v = round(float(s) * 100)
    except ValueError:
        return None
    return v if v > 0 else None

def fmt_dt(ts): return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y %H:%M:%S") if ts else "-"
def fmt_dm(ts): return datetime.fromtimestamp(ts, TZ).strftime("%d/%m às %H:%M") if ts else "-"

def short_name(name):
    p = (name or "").split()
    return (p[0] + (f" {p[-1][0]}." if len(p) > 1 else "")) if p else "Usuário"

def lotno(l): return f"{(l['lot_number'] or l['id']):03d}"

def badge(l):
    if l["status"] != "open": return ("Arrematado", "sold") if l["winner_id"] else ("Encerrado", "closed")
    if "auction_start" in l.keys() and l["auction_start"] and l["auction_start"] > now(): return ("Em breve", "upcoming")
    return ("Encerrando", "soon") if l["end_ts"] - now() < SOON else ("Aberto para lances", "open")

def astatus(a):
    if a["start_ts"] and a["start_ts"] > now(): return ("Em breve", "upcoming")
    if a["nopen"]: return ("Aberto para lances", "open")
    return ("Encerrado", "closed")

def auc_code(a): return f"{a['id']:04d}"

def lot_extra(f):
    pct = (f.get("comissao") or "5").replace(",", ".").replace("%", "").strip()
    try: pct = max(0.0, min(30.0, float(pct)))
    except ValueError: pct = 5.0
    return dict(specs=(f.get("ficha") or "").strip()[:3000], appraisal_cents=parse_money(f.get("avaliacao")),
                commission_pct=pct, fees=(f.get("taxas") or DEF_FEES).strip()[:1000],
                tags=clean_tags(f.get("tags")), note=(f.get("observacao") or "").strip()[:60])

def clean_tags(t):
    out = []
    for x in re.split(r"[,;\n]", t or ""):
        x = x.strip()[:24]
        if x and x.lower() not in [o.lower() for o in out]: out.append(x)
    return ", ".join(out[:6])

def tag_list(t): return [x.strip() for x in (t or "").split(",") if x.strip()]

def parse_specs(t):
    out = []
    for line in (t or "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() and v.strip(): out.append((k.strip(), v.strip()))
    return out

def parse_start(f):
    if f.get("abertura"):
        try: return int(datetime.strptime(f["abertura"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ).timestamp())
        except ValueError: return None
    return now()

def parse_end(f):
    dur = f.get("duracao") or ""
    if dur.isdigit(): return now() + int(dur) * 60
    if f.get("termino"):
        try: return int(datetime.strptime(f["termino"], "%Y-%m-%dT%H:%M").replace(tzinfo=TZ).timestamp())
        except ValueError: return None
    return None

def fmt_dmy(ts): return datetime.fromtimestamp(ts, TZ).strftime("%d/%m/%Y %H:%M") if ts else "-"
def fmt_wd(ts): return WEEKDAYS[datetime.fromtimestamp(ts, TZ).weekday()] if ts else ""
app.jinja_env.filters.update(brl=brl, dt=fmt_dt, dm=fmt_dm, dmy=fmt_dmy, wd=fmt_wd, short=short_name, year=lambda ts: datetime.fromtimestamp(ts, TZ).year)
def qs(f, **kw):
    d = {k: v for k, v in {**f, **kw}.items() if v}
    return "?" + urlencode(d)

app.jinja_env.globals.update(qs=qs, icon=icon, lotno=lotno, badge=badge, astatus=astatus, auc_code=auc_code, parse_specs=parse_specs, CAT_ICON=CAT_ICON, MODALIDADES=MODALIDADES,
                             CAT_SLUG=CAT_SLUG, GRUPOS=GRUPOS, MOD_LABEL=MOD_LABEL, tag_list=tag_list, lot_share=lambda l, cur=None: lot_share(l, cur), base_url=lambda: base_url(), wa_svg=lambda size=18, fill='currentColor': wa_svg(size, fill))

def close_expired(c=None):
    own = c is None
    c = c or connect()
    try:
        t = now()
        for lot in c.execute("SELECT id FROM lots WHERE status='open' AND end_ts<=?", (t,)).fetchall():
            top = c.execute("SELECT user_id, amount_cents FROM bids WHERE lot_id=? ORDER BY amount_cents DESC, id ASC LIMIT 1", (lot["id"],)).fetchone()
            cur = c.execute("UPDATE lots SET status='closed', closed_at=?, winner_id=?, final_cents=? WHERE id=? AND status='open'",
                      (t, top["user_id"] if top else None, top["amount_cents"] if top else None, lot["id"]))
            if top and cur.rowcount:
                try: create_nota(c, lot["id"], top["user_id"])
                except Exception as e: print("nota auto:", lot["id"], type(e).__name__, e, flush=True)
    finally:
        if own: c.close()

def closer_loop():
    while True:
        try: close_expired()
        except Exception as e: print("closer:", e, flush=True)
        time.sleep(10)

def current_user():
    uid = session.get("uid")
    if not uid: return None
    if "user" not in g:
        g.user = db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if g.user is None: session.clear()
    return g.user

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not current_user():
            if request.headers.get("X-Requested-With") == "fetch":
                return jsonify(ok=False, msg="Entre na sua conta para continuar.", login=True), 401
            flash("Entre na sua conta para continuar.", "warn")
            return redirect(url_for("login", next=request.path))
        return f(*a, **k)
    return w

def admin_required(f):
    @wraps(f)
    def w(*a, **k):
        u = current_user()
        if not u or not u["is_admin"]: abort(403)
        return f(*a, **k)
    return w

def csrf_token():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

def login_session(uid):
    tok = session.get("csrf"); session.clear()
    session["csrf"] = tok or secrets.token_hex(16); session["uid"] = uid; session.permanent = True

def client_ip():
    return (request.remote_addr or "?")[:64]

def log_event(kind, email="", until_ts=None, c=None):
    (c or db()).execute("INSERT INTO login_log(ts,ip,email,kind,until_ts) VALUES(?,?,?,?,?)", (now(), client_ip(), (email or "")[:120], kind, until_ts))

def is_locked(email):
    t = now(); c = db()
    return c.execute("SELECT 1 FROM login_log WHERE kind='lock' AND until_ts>? AND (ip=? OR (email<>'' AND email=?)) LIMIT 1",
                     (t, client_ip(), email)).fetchone() is not None

def register_fail(email, kind="fail"):
    c = db(); t = now(); log_event(kind, email)
    since = t - FAIL_WINDOW
    n_ip = c.execute("SELECT COUNT(*) FROM login_log WHERE kind IN ('fail','fail2fa') AND ts>? AND ip=? AND ts>COALESCE((SELECT MAX(ts) FROM login_log WHERE kind='lock' AND ip=?),0)", (since, client_ip(), client_ip())).fetchone()[0]
    n_em = c.execute("SELECT COUNT(*) FROM login_log WHERE kind IN ('fail','fail2fa') AND ts>? AND email=? AND email<>'' AND ts>COALESCE((SELECT MAX(ts) FROM login_log WHERE kind='lock' AND email=?),0)", (since, email, email)).fetchone()[0]
    if n_ip >= MAX_FAILS or n_em >= MAX_FAILS:
        log_event("lock", email if n_em >= MAX_FAILS else "", t + LOCK_SECS)

LOCK_MSG = "Muitas tentativas de acesso. Por segurança, o login foi bloqueado temporariamente. Tente novamente em 15 minutos."
FAIL_MSG = "E-mail ou senha incorretos."

@app.before_request
def before():
    if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
        abort(400, "Sessão expirada. Volte e tente de novo.")
    u = current_user()
    if u and u["is_admin"]:
        last = session.get("seen", now())
        if now() - last > ADMIN_IDLE:
            session.clear(); g.pop("user", None)
            flash("Sua sessão de administrador expirou por inatividade. Entre novamente.", "warn")
            return redirect(url_for("login", next=request.path))
        session["seen"] = now()
    close_expired(db())

@app.after_request
def sec_headers(resp):
    h = resp.headers
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if ON_RAILWAY or request.is_secure:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith(("/admin", "/entrar", "/minha-conta", "/nova-senha", "/nota")):
        h["Cache-Control"] = "no-store"
    return resp

def get_cfg():
    if "cfg" not in g:
        d = {r["k"]: r["v"] for r in db().execute("SELECT k, v FROM settings") if (r["v"] or "").strip()}
        d.setdefault("empresa", "SP Leilões")
        d["wa_link"] = ("https://wa.me/" + re.sub(r"\D", "", d["whatsapp"])) if d.get("whatsapp") else ""
        g.cfg = d
    return g.cfg

app.jinja_env.globals["MAX_PHOTOS"] = MAX_PHOTOS

HAS_CRUMB = {"lot", "auction", "edital", "habilitar", "edit_lot", "edit_auction", "admin_client", "admin_settings", "admin_home"}

def back_target():
    """Destino lógico do botão Voltar (usado quando não há página anterior do próprio site)."""
    ep = request.endpoint or ""; va = request.view_args or {}; path = request.path
    if ep in ("index", "health", "static", "uploads") or path == "/": return None
    if ep == "lot":
        l = db().execute("SELECT l.auction_id, a.title FROM lots l LEFT JOIN auctions a ON a.id=l.auction_id WHERE l.id=?", (va.get("lid"),)).fetchone()
        if l and l["auction_id"]: return (f"/leilao/{l['auction_id']}", l["title"] or "Leilão")
        return ("/lotes", "Lotes")
    if ep == "edit_lot": return (f"/lote/{va.get('lid')}", "Lote")
    if ep in ("edital", "habilitar", "edit_auction"): return (f"/leilao/{va.get('aid')}", "Leilão")
    if ep == "auction": return ("/", "Início")
    if ep == "admin_client": return ("/admin/clientes", "Clientes")
    if path.startswith("/admin/") or ep in ("new_lot", "new_auction"): return ("/admin", "Administração")
    if path.startswith("/minha-conta/"): return ("/minha-conta", "Minha conta")
    if ep in ("password_reset", "login_2fa"): return ("/entrar", "Entrar")
    if ep == "password_forgot": return ("/entrar", "Entrar")
    if ep in ("email_pending",): return ("/minha-conta", "Minha conta")
    return ("/", "Início")

@app.after_request
def mark_post(resp):
    # depois de um envio de formulário (POST), o Voltar usa o destino lógico, sem voltar ao formulário
    if request.method == "POST" and resp.status_code < 400:
        session["_after_post"] = 1
    return resp

@app.context_processor
def ctx():
    bt = back_target() if request.endpoint else None
    after_post = bool(session.pop("_after_post", None)) if "_after_post" in session else False
    return dict(cfg=get_cfg(), me=current_user(), csrf=csrf_token, CATEGORIAS=CATEGORIAS, server_now=now(), VERSION=VERSION,
                back=bt, back_after_post=after_post, back_crumb=bt and bt[0] != "/" and request.endpoint not in HAS_CRUMB)

def lot_state(c, lot):
    top = c.execute("SELECT b.amount_cents, b.user_id, u.name FROM bids b JOIN users u ON u.id=b.user_id WHERE lot_id=? ORDER BY amount_cents DESC, b.id ASC LIMIT 1", (lot["id"],)).fetchone()
    cur = top["amount_cents"] if top else None
    nxt = cur + lot["inc_cents"] if cur else lot["start_cents"]
    n = c.execute("SELECT COUNT(*) FROM bids WHERE lot_id=?", (lot["id"],)).fetchone()[0]
    return dict(current=cur, min_next=nxt, nbids=n, leader_id=top["user_id"] if top else None,
                leader=short_name(top["name"]) if top else None)

def remove_lot(c, lot_id):
    for p in c.execute("SELECT filename FROM photos WHERE lot_id=?", (lot_id,)).fetchall():
        if not p["filename"].startswith("ex_"):
            try: os.remove(os.path.join(UPLOADS, p["filename"]))
            except OSError: pass
    for t in ("photos", "bids", "favorites"): c.execute(f"DELETE FROM {t} WHERE lot_id=?", (lot_id,))
    c.execute("DELETE FROM lots WHERE id=?", (lot_id,))

def end_of_today():
    d = datetime.now(TZ).replace(hour=23, minute=59, second=59)
    return int(d.timestamp())

def my_favs():
    me = current_user()
    return {r[0] for r in db().execute("SELECT lot_id FROM favorites WHERE user_id=?", (me["id"],))} if me else set()

# ---------- páginas ----------
@app.get("/health")
def health(): return {"ok": True, "app": "sp-leiloes", "version": VERSION}

@app.get("/uploads/<path:name>")
def uploads(name): return send_from_directory(UPLOADS, name, max_age=86400)

def site_img(slug):
    fn = get_cfg().get("img_" + slug)
    return ("/uploads/" + fn) if fn else f"/static/stock/{slug}.jpg"
app.jinja_env.globals["site_img"] = site_img

@app.get("/")
def index():
    c = db(); t = now()
    cats = {r[0]: r[1] for r in c.execute("SELECT category, COUNT(*) FROM lots WHERE status='open' GROUP BY category")}
    banners = c.execute("SELECT * FROM banners WHERE active=1 ORDER BY pos, id").fetchall()
    upcoming = c.execute(AUCQ + " WHERE nopen>0 ORDER BY end_ts LIMIT 8").fetchall()
    brows = banner_rows(c, banners, upcoming)
    sections = []
    for a in upcoming[:4]:
        lots = c.execute(LOTQ + " WHERE auction_id=? ORDER BY status='open' DESC, lot_number, id LIMIT 40", (a["id"],)).fetchall()
        if lots: sections.append((a, lots))
    prox_lotes = c.execute(LOTQ + " WHERE status='open' ORDER BY COALESCE(auction_start,0)>? DESC, end_ts, lot_number, id LIMIT 24", (t,)).fetchall()
    destaques = []
    for slug, label, ic, cs in GRUPOS:
        q = ",".join("?" * len(cs))
        lots = c.execute(LOTQ + f" WHERE status='open' AND category IN ({q}) ORDER BY nb DESC, end_ts LIMIT 12", cs).fetchall()
        destaques.append((slug, label, ic, lots))
    return render_template("index.html", cats=cats, banners=banners, upcoming=upcoming, sections=sections,
                           destaques=destaques, prox_lotes=prox_lotes, brows=brows, favs=my_favs())

def _bchips(ts):
    if not ts: return []
    d = datetime.fromtimestamp(ts, TZ)
    return [d.strftime("%d/%m"), d.strftime("%Hh%M"), WEEKDAYS[d.weekday()][:3].upper()]

def banner_rows(c, banners, upcoming):
    """3 fileiras de banners: os enviados no admin (por fileira) e, nas fileiras vazias,
    banners automáticos dos próximos leilões e lotes (foto + texto)."""
    rows = [[], [], []]
    for b in banners:
        f = b["fila"] if b["fila"] in (1, 2, 3) else 1
        rows[f - 1].append(dict(kind="img", img=b["filename"], link=b["link"], title=b["title"]))
    empty = [i for i in range(3) if not rows[i]]
    if not empty: return rows
    auto = []
    for a in upcoming[:6]:
        sub = f"{a['nlots']} lote{'' if a['nlots'] == 1 else 's'}" + (f" · {a['location']}" if a["location"] else "")
        auto.append(dict(kind="auto", img=a["cover"], link=f"/leilao/{a['id']}", all_link=f"/leilao/{a['id']}",
                         kicker=f"Leilão nº {auc_code(a)}", title=a["title"], sub=sub,
                         mod=MOD_LABEL.get(a["modality"] or "Online", (a["modality"] or "Online").upper()),
                         chips=_bchips(a["end_ts"]), soon=bool(a["start_ts"] and a["start_ts"] > now())))
    for l in c.execute(LOTQ + " WHERE status='open' AND photo IS NOT NULL ORDER BY nb DESC, end_ts, lot_number LIMIT 12"):
        price = ("Maior lance " if l["cur"] else "Lance inicial ") + brl(l["price"])
        auto.append(dict(kind="auto", img=l["photo"], link=f"/lote/{l['id']}",
                         all_link=f"/leilao/{l['auction_id']}" if l["auction_id"] else "/lotes",
                         kicker=f"Lote {lotno(l)} · {l['category']}", title=l["title"], sub=price,
                         mod=MOD_LABEL.get(l["auction_mod"] or "Online", (l["auction_mod"] or "Online").upper()),
                         chips=_bchips(l["end_ts"]), soon=bool(l["auction_start"] and l["auction_start"] > now())))
    per = max(1, min(6, -(-len(auto) // len(empty))))
    for k, it in enumerate(auto[:per * len(empty)]):
        rows[empty[k % len(empty)]].append(it)
    return rows

ORDENS = {"encerrando": "status='open' DESC, end_ts ASC", "menor": "price ASC", "maior": "price DESC",
          "lances": "nb DESC, end_ts ASC", "recentes": "created_at DESC, id DESC", "numero": "lot_number, id"}

def lot_filters(a, extra_where="", extra_args=()):
    q = (a.get("q") or "").strip(); cat = a.get("cat") or ""; st = a.get("status") or "abertos"
    grupo = a.get("grupo") if a.get("grupo") in GRUPO else ""
    ordem = a.get("ordem") if a.get("ordem") in ORDENS else "encerrando"
    w, args = ["1=1"], []
    if st == "abertos": w.append("status='open'")
    elif st == "encerrando": w.append("status='open' AND end_ts<=?"); args.append(now() + SOON)
    elif st == "encerrados": w.append("status='closed'")
    if q: w.append("(title LIKE ? OR description LIKE ?)"); args += [f"%{q}%", f"%{q}%"]
    if cat in CATEGORIAS: w.append("category=?"); args.append(cat)
    elif grupo:
        cs = GRUPO[grupo][3]; w.append("category IN (" + ",".join("?" * len(cs)) + ")"); args += cs
    lote = re.sub(r"\D", "", a.get("lote") or "")[:6]
    if lote: w.append("COALESCE(lot_number,id)=?"); args.append(int(lote))
    tag = (a.get("tag") or "").strip()[:24]
    if tag: w.append("(','||REPLACE(COALESCE(tags,''),', ',',')||',') LIKE ?"); args.append(f"%,{tag},%")
    mn, mx = parse_money(a.get("min")), parse_money(a.get("max"))
    if mn: w.append("price>=?"); args.append(mn)
    if mx: w.append("price<=?"); args.append(mx)
    if extra_where: w.append(extra_where); args += list(extra_args)
    if st == "encerrados" and ordem == "encerrando": order = "closed_at DESC"
    else: order = ORDENS[ordem]
    rows = db().execute(LOTQ + " WHERE " + " AND ".join(w) + " ORDER BY " + order + " LIMIT 150", args).fetchall()
    return rows, dict(q=q, cat=cat if cat in CATEGORIAS else "", grupo=grupo, status=st, ordem=ordem, min=a.get("min") or "", max=a.get("max") or "", lote=lote, tag=tag)

def tags_in(where="status='open'", args=()):
    seen = {}
    for r in db().execute("SELECT tags FROM lots WHERE " + where + " AND COALESCE(tags,'')<>''", args):
        for x in tag_list(r[0]): seen.setdefault(x.lower(), x)
    return sorted(seen.values(), key=str.lower)[:40]

@app.get("/lotes")
def lots_page():
    rows, f = lot_filters(request.args)
    return render_template("lots.html", lots=rows, f=f, favs=my_favs(), tags_all=tags_in())

@app.get("/leiloes")
def auctions_page():
    c = db()
    return render_template("auctions.html", open_=c.execute(AUCQ + " WHERE nopen>0 ORDER BY COALESCE(start_ts,0)>?, next_end", (now(),)).fetchall(),
                           closed=c.execute(AUCQ + " WHERE nopen=0 ORDER BY end_ts DESC LIMIT 30").fetchall())

@app.get("/leilao/<int:aid>")
def auction(aid):
    a = db().execute(AUCQ + " WHERE id=?", (aid,)).fetchone()
    if not a: abort(404)
    args = dict(request.args); args.setdefault("status", "todos"); args.setdefault("ordem", "numero")
    rows, f = lot_filters(args, "auction_id=?", (aid,))
    me = current_user()
    hab = bool(me and db().execute("SELECT 1 FROM habilitations WHERE user_id=? AND auction_id=?", (me["id"], aid)).fetchone())
    return render_template("auction.html", a=a, lots=rows, f=f, favs=my_favs(), hab=hab, tags_all=tags_in("auction_id=?", (aid,)))

@app.get("/leilao/<int:aid>/edital")
def edital(aid):
    a = db().execute(AUCQ + " WHERE id=?", (aid,)).fetchone()
    if not a: abort(404)
    return render_template("edital.html", a=a, DEF_EDITAL=DEF_EDITAL)

@app.route("/leilao/<int:aid>/habilitar", methods=["GET", "POST"])
@login_required
def habilitar(aid):
    c = db(); me = current_user()
    a = c.execute(AUCQ + " WHERE id=?", (aid,)).fetchone()
    if not a: abort(404)
    hab = c.execute("SELECT 1 FROM habilitations WHERE user_id=? AND auction_id=?", (me["id"], aid)).fetchone()
    gt = None if hab else gate(me, request.full_path.rstrip("?"))
    if gt:
        flash(gt[0], "warn"); return redirect(gt[1])
    if request.method == "POST" and not hab:
        if not request.form.get("aceite"):
            flash("Para se habilitar, aceite o edital do leilão.", "err")
        else:
            c.execute("INSERT OR IGNORE INTO habilitations(user_id,auction_id,created_at) VALUES(?,?,?)", (me["id"], aid, now()))
            flash("Habilitação aprovada! Você já pode dar lances neste leilão.", "ok")
            nxt = request.form.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("auction", aid=aid))
    return render_template("habilitar.html", a=a, hab=hab, DEF_EDITAL=DEF_EDITAL, nxt=request.args.get("next") or request.form.get("next") or "")

@app.get("/agenda")
def agenda():
    rows = db().execute(AUCQ + " WHERE end_ts > ? ORDER BY COALESCE(start_ts, end_ts)", (now() - 7 * 86400,)).fetchall()
    days = {}
    for a in rows:
        d = datetime.fromtimestamp(a["end_ts"], TZ).date(); days.setdefault(d, []).append(a)
    today = datetime.now(TZ).date(); m1 = today.replace(day=1); months = []
    for k in range(2):
        start = m1 - timedelta(days=(m1.weekday() + 1) % 7)
        months.append((m1, [[start + timedelta(days=w * 7 + i) for i in range(7)] for w in range(6)]))
        m1 = (m1.replace(day=28) + timedelta(days=4)).replace(day=1)
    MES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
    return render_template("agenda.html", days=sorted(days.items()), marked=set(days.keys()), months=months, today=today, MES=MES)

@app.route("/leiloes/novo", methods=["GET", "POST"])
@app.route("/leilao/novo", methods=["GET", "POST"])
@admin_required
def new_auction():
    f = request.form
    if request.method == "POST":
        title = (f.get("titulo") or "").strip()[:120]; end_ts = parse_end(f); start_ts = parse_start(f); err = None
        if len(title) < 3: err = "Informe o nome do leilão."
        elif not end_ts or end_ts < now() + 120: err = "O encerramento precisa ser pelo menos 2 minutos à frente."
        elif end_ts > now() + 30 * 86400: err = "O encerramento pode ser no máximo 30 dias à frente."
        elif not start_ts or start_ts >= end_ts: err = "A abertura precisa ser antes do encerramento."
        if err:
            flash(err, "err"); return render_template("new_auction.html", f=f, DEF_EDITAL=DEF_EDITAL), 400
        mod = f.get("modalidade") if f.get("modalidade") in MODALIDADES else "Online"
        cur = db().execute("INSERT INTO auctions(seller_id,title,description,location,end_ts,created_at,start_ts,modality,edital,ribbon) VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (current_user()["id"], title, (f.get("descricao") or "").strip()[:3000], (f.get("local") or "").strip()[:120], end_ts, now(),
                            start_ts, mod, (f.get("edital") or DEF_EDITAL).strip()[:8000], (f.get("selo") or "").strip()[:20]))
        flash("Leilão criado! Agora adicione os lotes.", "ok")
        return redirect(url_for("new_lot", leilao=cur.lastrowid))
    return render_template("new_auction.html", f={}, DEF_EDITAL=DEF_EDITAL)

@app.get("/lote/<int:lid>")
def lot(lid):
    c = db()
    c.execute("UPDATE lots SET views=COALESCE(views,0)+1 WHERE id=?", (lid,))
    l = c.execute("SELECT l.*, u.name seller_name, a.title auction_title, a.location auction_location, a.start_ts auction_start, a.modality auction_modality FROM lots l JOIN users u ON u.id=l.seller_id LEFT JOIN auctions a ON a.id=l.auction_id WHERE l.id=?", (lid,)).fetchone()
    if not l: abort(404)
    me = current_user()
    hab = (not l["auction_id"]) or bool(me and c.execute("SELECT 1 FROM habilitations WHERE user_id=? AND auction_id=?", (me["id"], l["auction_id"])).fetchone())
    nhab = c.execute("SELECT COUNT(*) FROM habilitations WHERE auction_id=?", (l["auction_id"],)).fetchone()[0] if l["auction_id"] else None
    photos = [r["filename"] for r in c.execute("SELECT filename FROM photos WHERE lot_id=? ORDER BY pos", (lid,))]
    winner = c.execute("SELECT id, name, email, phone FROM users WHERE id=?", (l["winner_id"],)).fetchone() if l["winner_id"] else None
    nfav = c.execute("SELECT COUNT(*) FROM favorites WHERE lot_id=?", (lid,)).fetchone()[0]
    related = c.execute(LOTQ + " WHERE status='open' AND id<>? AND (auction_id=? OR category=?) ORDER BY auction_id=? DESC, end_ts LIMIT 4",
                        (lid, l["auction_id"] or -1, l["category"], l["auction_id"] or -1)).fetchall()
    notas_lot = c.execute("SELECT id, user_id, seq, year, status FROM notas WHERE lot_id=?", (lid,)).fetchall()
    nav = None
    if l["auction_id"]:
        ids = [r[0] for r in c.execute("SELECT id FROM lots WHERE auction_id=? ORDER BY COALESCE(lot_number,id), id", (l["auction_id"],))]
        if lid in ids:
            i = ids.index(lid); nav = dict(prev=ids[i-1] if i > 0 else None, next=ids[i+1] if i < len(ids)-1 else None, pos=i+1, total=len(ids))
    return render_template("lot.html", nav=nav, notas_lot=notas_lot, l=l, st=lot_state(c, l), photos=photos, winner=winner, bids=bid_list(c, lid),
                           is_fav=lid in my_favs(), nfav=nfav, related=related, favs=my_favs(), hab=hab, nhab=nhab,
                           upcoming=bool(l["auction_start"] and l["auction_start"] > now()), gt=gate(me, f"/lote/{lid}"))

def bid_list(c, lid):
    return [dict(id=b["id"], user=short_name(b["name"]), amount=brl(b["amount_cents"]),
                 at=fmt_dt(b["created_at"]), ext=bool(b["extended"]))
            for b in c.execute("SELECT b.*, u.name FROM bids b JOIN users u ON u.id=b.user_id WHERE lot_id=? ORDER BY b.amount_cents DESC, b.id DESC LIMIT 50", (lid,))]

@app.get("/api/lote/<int:lid>")
def api_lot(lid):
    c = db()
    l = c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone()
    if not l: return jsonify(error="not found"), 404
    st = lot_state(c, l); me = current_user()
    w = c.execute("SELECT name FROM users WHERE id=?", (l["winner_id"],)).fetchone() if l["winner_id"] else None
    return jsonify(status=l["status"], end_ts=l["end_ts"], server_now=now(), current=brl(st["current"]) if st["current"] else None, current_cents=st["current"],
                   min_next=brl(st["min_next"]), min_next_cents=st["min_next"], inc_cents=l["inc_cents"], nbids=st["nbids"], leader=st["leader"],
                   you_lead=bool(me and st["leader_id"] == me["id"]), winner=short_name(w["name"]) if w else None,
                   you_won=bool(me and l["winner_id"] == me["id"]), bids=bid_list(c, lid))

@app.post("/lote/<int:lid>/lance")
@login_required
def bid(lid):
    me = current_user(); amount = parse_money(request.form.get("valor"))
    ajax = request.headers.get("X-Requested-With") == "fetch"
    c = db(); msg, ok = None, False
    gt = gate(me, url_for("lot", lid=lid))
    if gt:
        if ajax: return jsonify(ok=False, msg=gt[0], redirect=gt[1]), 403
        flash(gt[0], "warn"); return redirect(gt[1])
    c.execute("BEGIN IMMEDIATE")
    try:
        l = c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone()
        auc = c.execute("SELECT start_ts FROM auctions WHERE id=?", (l["auction_id"],)).fetchone() if l and l["auction_id"] else None
        t = now()
        if not l: msg = "Lote não encontrado."
        elif l["status"] != "open" or t >= l["end_ts"]: msg = "Este lote já foi encerrado."
        elif l["seller_id"] == me["id"]: msg = "Você não pode dar lance no seu próprio lote."
        elif auc and auc["start_ts"] and auc["start_ts"] > t: msg = "Este leilão ainda não abriu para lances."
        elif l["auction_id"] and not c.execute("SELECT 1 FROM habilitations WHERE user_id=? AND auction_id=?", (me["id"], l["auction_id"])).fetchone():
            msg = "Habilite-se neste leilão para dar lances."
        elif amount is None: msg = "Informe um valor válido."
        else:
            st = lot_state(c, l)
            if st["leader_id"] == me["id"]: msg = "Você já tem o maior lance."
            elif amount < st["min_next"]: msg = f"O lance mínimo agora é {brl(st['min_next'])}."
            elif amount > max(st["min_next"] * 20, 100_000_00): msg = "Valor muito alto — confira se digitou certo."
            else:
                ext = 0
                if l["end_ts"] - t <= EXTEND_WINDOW:
                    c.execute("UPDATE lots SET end_ts=end_ts+? WHERE id=?", (EXTEND_BY, lid)); ext = 1
                c.execute("INSERT INTO bids(lot_id,user_id,amount_cents,created_at,extended) VALUES(?,?,?,?,?)", (lid, me["id"], amount, t, ext))
                ok = True
                msg = f"Lance de {brl(amount)} registrado!" + (" O prazo foi prorrogado em 2 minutos." if ext else "")
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK"); raise
    if ajax: return jsonify(ok=ok, msg=msg), (200 if ok else 400)
    flash(msg, "ok" if ok else "err")
    return redirect(url_for("lot", lid=lid))

@app.post("/lote/<int:lid>/favoritar")
@login_required
def favorite(lid):
    me = current_user(); c = db()
    if not c.execute("SELECT 1 FROM lots WHERE id=?", (lid,)).fetchone(): abort(404)
    if c.execute("SELECT 1 FROM favorites WHERE user_id=? AND lot_id=?", (me["id"], lid)).fetchone():
        c.execute("DELETE FROM favorites WHERE user_id=? AND lot_id=?", (me["id"], lid)); fav = False
    else:
        c.execute("INSERT INTO favorites(user_id,lot_id,created_at) VALUES(?,?,?)", (me["id"], lid, now())); fav = True
    if request.headers.get("X-Requested-With") == "fetch": return jsonify(ok=True, fav=fav)
    return redirect(request.form.get("next") or url_for("lot", lid=lid))

def my_auctions(c, me, min_end=0):
    if me["is_admin"]: return c.execute("SELECT * FROM auctions WHERE end_ts>? ORDER BY end_ts", (min_end,)).fetchall()
    return c.execute("SELECT * FROM auctions WHERE seller_id=? AND end_ts>? ORDER BY end_ts", (me["id"], min_end)).fetchall()

@app.route("/lote/novo", methods=["GET", "POST"])
@app.route("/lotes/novo", methods=["GET", "POST"])
@admin_required
def new_lot():
    f = request.form; c = db(); me = current_user()
    auctions = my_auctions(c, me, now() + 120)
    if request.method == "POST":
        title = (f.get("titulo") or "").strip()[:120]; desc = (f.get("descricao") or "").strip()[:4000]
        cat = f.get("categoria") if f.get("categoria") in CATEGORIAS else "Veículos"
        start = parse_money(f.get("lance_inicial")); inc = parse_money(f.get("incremento"))
        aid = int(f["leilao"]) if (f.get("leilao") or "").isdigit() else None
        auc = next((a for a in auctions if a["id"] == aid), None)
        lot_number = None
        if auc:
            lot_number = (c.execute("SELECT MAX(lot_number) FROM lots WHERE auction_id=?", (aid,)).fetchone()[0] or 0) + 1
            end_ts = auc["end_ts"] + (lot_number - 1) * STAGGER
        else:
            end_ts = parse_end(f)
        err = None
        if aid and not auc: err = "Leilão inválido ou já encerrando."
        elif len(title) < 3: err = "Informe um título."
        elif not start: err = "Informe o lance inicial."
        elif not inc: err = "Informe o incremento mínimo."
        elif not end_ts or end_ts < now() + 120: err = "O término precisa ser pelo menos 2 minutos à frente."
        elif end_ts > now() + 31 * 86400: err = "O término pode ser no máximo 30 dias à frente."
        saved = []
        if not err: saved, err = save_photos(request.files.getlist("fotos"))
        if err:
            return form_error(err, "new_lot.html", f=f, auctions=auctions, DEF_COND=DEF_COND, DEF_PICK=DEF_PICK, DEF_FEES=DEF_FEES)
        x = lot_extra(f)
        cur = c.execute("INSERT INTO lots(seller_id,title,description,category,start_cents,inc_cents,end_ts,created_at,auction_id,lot_number,conditions,pickup,specs,appraisal_cents,commission_pct,fees,tags,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (me["id"], title, desc, cat, start, inc, end_ts, now(), aid if auc else None, lot_number,
                         (f.get("condicoes") or DEF_COND).strip()[:3000], (f.get("retirada") or DEF_PICK).strip()[:2000],
                         x["specs"], x["appraisal_cents"], x["commission_pct"], x["fees"], x["tags"], x["note"]))
        for i, n in enumerate(saved):
            c.execute("INSERT INTO photos(lot_id,filename,pos) VALUES(?,?,?)", (cur.lastrowid, n, i))
        flash("Lote publicado!" + (f" {len(saved)} foto(s) adicionada(s)." if saved else ""), "ok")
        return form_ok(url_for("lot", lid=cur.lastrowid))
    return render_template("new_lot.html", f={"leilao": request.args.get("leilao", "")}, auctions=auctions, DEF_COND=DEF_COND, DEF_PICK=DEF_PICK, DEF_FEES=DEF_FEES)

@app.post("/lote/<int:lid>/remover")
@admin_required
def delete_lot(lid):
    me = current_user(); c = db()
    l = c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone()
    if not l: abort(404)
    remove_lot(c, lid); flash("Lote removido.", "ok")
    return redirect(request.form.get("next") or url_for("index"))

OK_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "GIF", "HEIF", "HEIC"}

def process_image(data, fn):
    """Valida o tipo REAL (Pillow + pillow-heif), corrige rotação, redimensiona (máx. 1600 px) e
    regrava como JPEG 85% (remove EXIF/GPS). Retorna (bytes_jpeg, None) ou (None, mensagem)."""
    if not data:
        return None, (f"A foto “{fn}” chegou vazia (0 bytes). Se ela estiver só na nuvem (Google Fotos, OneDrive, "
                      "Samsung Cloud), baixe-a para o aparelho ou tire a foto pela câmera e tente de novo.")
    if len(data) > MAX_PHOTO: return None, f"A foto “{fn}” tem mais de 20 MB. Reduza a resolução da câmera ou envie outra."
    try:
        im = Image.open(io.BytesIO(data)); fmt = im.format
        if fmt not in OK_FORMATS:
            return None, f"“{fn}” não é um formato aceito ({fmt}). Use JPG, PNG, WEBP, HEIC ou GIF."
        w, h = im.size
        if fmt in ("JPEG", "MPO"): im.draft("RGB", (PHOTO_SIDE * 2, PHOTO_SIDE * 2))   # decodifica já reduzida
        elif w * h > 60_000_000: return None, f"“{fn}”: resolução grande demais."
        im.load()
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P", "PA"):
            im = im.convert("RGBA"); bg = Image.new("RGB", im.size, (255, 255, 255)); bg.paste(im, mask=im.split()[-1]); im = bg
        im = im.convert("RGB"); im.thumbnail((PHOTO_SIDE, PHOTO_SIDE), Image.LANCZOS)
        out = io.BytesIO(); im.save(out, "JPEG", quality=85, optimize=True, progressive=True); out = out.getvalue()
        chk = Image.open(io.BytesIO(out)); chk.verify()                       # confere o resultado
        if chk.format != "JPEG": raise ValueError("saída inválida")
        return out, None
    except Image.DecompressionBombError:
        return None, f"“{fn}”: resolução grande demais."
    except Exception:
        return None, f"“{fn}” não é uma imagem válida ou está corrompida. Use JPG, PNG, WEBP, HEIC ou GIF."

def save_photos(files, room=MAX_PHOTOS):
    """Processa as fotos enviadas. Retorna (nomes_salvos, erro). Em caso de erro nada fica salvo."""
    files = [x for x in files if x and x.filename]
    saved, errs, info = [], [], []
    if len(files) > room:
        errs.append(f"Máximo de {MAX_PHOTOS} fotos por lote: você pode adicionar mais {max(room, 0)}.")
    else:
        for x in files:
            data = x.stream.read(MAX_PHOTO + 1)
            out, err = process_image(data, (x.filename or "foto")[-60:])
            info.append(f"{len(data) // 1024}KB→{len(out) // 1024 if out else 'ERRO'}")
            if err: errs.append(err); continue
            name = secrets.token_hex(12) + ".jpg"
            with open(os.path.join(UPLOADS, name), "wb") as fh: fh.write(out)
            saved.append(name)
    if files or request.files:
        print(f"upload {request.path}: campos={len(request.files.getlist('fotos'))} arquivos={len(files)} [{', '.join(info)}] erros={len(errs)}", flush=True)
    if errs:
        for n in saved:
            try: os.remove(os.path.join(UPLOADS, n))
            except OSError: pass
        return [], " ".join(errs)
    return saved, None

def is_xhr(): return request.headers.get("X-Requested-With") == "fetch"

def form_error(err, template, **kw):
    if is_xhr(): return jsonify(ok=False, error=err), 400
    flash(err, "err"); return render_template(template, **kw), 400

def form_ok(url):
    return jsonify(ok=True, redirect=url) if is_xhr() else redirect(url)

def reorder_photos(c, lid, cover_id=None):
    ids = [r["id"] for r in c.execute("SELECT id FROM photos WHERE lot_id=? ORDER BY pos, id", (lid,))]
    if cover_id in ids: ids.remove(cover_id); ids.insert(0, cover_id)
    for i, pid in enumerate(ids): c.execute("UPDATE photos SET pos=? WHERE id=?", (i, pid))

@app.route("/admin/lote/<int:lid>/editar", methods=["GET", "POST"])
@admin_required
def edit_lot(lid):
    c = db()
    l = c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone()
    if not l: abort(404)
    nb = c.execute("SELECT COUNT(*) FROM bids WHERE lot_id=?", (lid,)).fetchone()[0]
    photos = c.execute("SELECT * FROM photos WHERE lot_id=? ORDER BY pos", (lid,)).fetchall()
    locked = nb > 0 or l["status"] != "open"
    if request.method == "POST":
        f = request.form; err = None
        title = (f.get("titulo") or "").strip()[:120]
        cat = f.get("categoria") if f.get("categoria") in CATEGORIAS else l["category"]
        start, inc, end_ts = l["start_cents"], l["inc_cents"], l["end_ts"]
        if not locked:
            start = parse_money(f.get("lance_inicial")); inc = parse_money(f.get("incremento"))
            if f.get("duracao") or f.get("termino"): end_ts = parse_end(f)
        if len(title) < 3: err = "Informe um título."
        elif not start or not inc: err = "Informe lance inicial e incremento."
        elif not locked and (not end_ts or end_ts < now() + 120): err = "O término precisa ser pelo menos 2 minutos à frente."
        removing = {str(p["id"]) for p in photos} & set(f.getlist("remover_foto"))
        saved = []
        if not err: saved, err = save_photos(request.files.getlist("fotos"), MAX_PHOTOS - (len(photos) - len(removing)))
        if err:
            return form_error(err, "edit_lot.html", l=l, nb=nb, photos=photos, locked=locked, MAX_PHOTOS=MAX_PHOTOS)
        x = lot_extra(f)
        if locked: x["commission_pct"] = l["commission_pct"]
        c.execute("UPDATE lots SET title=?, description=?, category=?, conditions=?, pickup=?, start_cents=?, inc_cents=?, end_ts=?, specs=?, appraisal_cents=?, commission_pct=?, fees=?, tags=?, note=? WHERE id=?",
                  (title, (f.get("descricao") or "").strip()[:4000], cat, (f.get("condicoes") or DEF_COND).strip()[:3000],
                   (f.get("retirada") or DEF_PICK).strip()[:2000], start, inc, end_ts, x["specs"], x["appraisal_cents"], x["commission_pct"], x["fees"], x["tags"], x["note"], lid))
        for pid in f.getlist("remover_foto"):
            p = c.execute("SELECT filename FROM photos WHERE id=? AND lot_id=?", (pid, lid)).fetchone()
            if p:
                c.execute("DELETE FROM photos WHERE id=?", (pid,))
                if not p["filename"].startswith("ex_"):
                    try: os.remove(os.path.join(UPLOADS, p["filename"]))
                    except OSError: pass
        base = c.execute("SELECT COALESCE(MAX(pos),-1)+1 FROM photos WHERE lot_id=?", (lid,)).fetchone()[0]
        new_ids = [c.execute("INSERT INTO photos(lot_id,filename,pos) VALUES(?,?,?)", (lid, n, base + i)).lastrowid for i, n in enumerate(saved)]
        capa = f.get("capa") or ""; cover = None
        if capa.startswith("e") and capa[1:].isdigit(): cover = int(capa[1:])
        elif capa == "n0" and new_ids: cover = new_ids[0]
        reorder_photos(c, lid, cover)
        flash("Lote atualizado." + (f" {len(saved)} foto(s) adicionada(s)." if saved else "") + (" (Preço e prazo não mudam porque o lote já tem lances.)" if locked else ""), "ok")
        return form_ok(url_for("lot", lid=lid))
    return render_template("edit_lot.html", l=l, nb=nb, photos=photos, locked=locked, MAX_PHOTOS=MAX_PHOTOS)

@app.route("/admin/leilao/<int:aid>/editar", methods=["GET", "POST"])
@admin_required
def edit_auction(aid):
    c = db()
    a = c.execute("SELECT * FROM auctions WHERE id=?", (aid,)).fetchone()
    if not a: abort(404)
    if request.method == "POST":
        f = request.form; title = (f.get("titulo") or "").strip()[:120]; end_ts = a["end_ts"]; err = None
        if f.get("duracao") or f.get("termino"): end_ts = parse_end(f)
        if len(title) < 3: err = "Informe o nome do leilão."
        elif end_ts != a["end_ts"] and (not end_ts or end_ts < now() + 120): err = "O encerramento precisa ser pelo menos 2 minutos à frente."
        if err:
            flash(err, "err"); return render_template("edit_auction.html", a=a, DEF_EDITAL=DEF_EDITAL), 400
        start_ts = parse_start(f) if f.get("abertura") else a["start_ts"]
        mod = f.get("modalidade") if f.get("modalidade") in MODALIDADES else (a["modality"] or "Online")
        c.execute("UPDATE auctions SET title=?, description=?, location=?, end_ts=?, start_ts=?, modality=?, edital=?, ribbon=? WHERE id=?",
                  (title, (f.get("descricao") or "").strip()[:3000], (f.get("local") or "").strip()[:120], end_ts, start_ts, mod,
                   (f.get("edital") or DEF_EDITAL).strip()[:8000], (f.get("selo") or "").strip()[:20], aid))
        moved = 0
        if end_ts != a["end_ts"]:
            for l in c.execute("SELECT id, lot_number FROM lots WHERE auction_id=? AND status='open' AND NOT EXISTS(SELECT 1 FROM bids WHERE lot_id=lots.id)", (aid,)).fetchall():
                c.execute("UPDATE lots SET end_ts=? WHERE id=?", (end_ts + ((l["lot_number"] or 1) - 1) * STAGGER, l["id"])); moved += 1
        flash("Leilão atualizado." + (f" Novo horário aplicado a {moved} lote(s) sem lances." if end_ts != a["end_ts"] else ""), "ok")
        return redirect(url_for("auction", aid=aid))
    return render_template("edit_auction.html", a=a, DEF_EDITAL=DEF_EDITAL)

@app.post("/admin/leilao/<int:aid>/remover")
@admin_required
def admin_del_auction(aid):
    c = db()
    c.execute("UPDATE lots SET auction_id=NULL WHERE auction_id=?", (aid,))
    c.execute("DELETE FROM auctions WHERE id=?", (aid,)); c.execute("DELETE FROM habilitations WHERE auction_id=?", (aid,))
    flash("Leilão removido (os lotes continuam como avulsos).", "ok")
    return redirect(url_for("admin"))

@app.get("/minha-conta")
@login_required
def account():
    me = current_user(); c = db()
    meus_lotes = c.execute(LOTQ + " WHERE seller_id=? ORDER BY created_at DESC", (me["id"],)).fetchall()
    lances = c.execute(LOTQ + " WHERE id IN (SELECT lot_id FROM bids WHERE user_id=?) ORDER BY status='open' DESC, end_ts ASC", (me["id"],)).fetchall()
    my_max = {r[0]: r[1] for r in c.execute("SELECT lot_id, MAX(amount_cents) FROM bids WHERE user_id=? GROUP BY lot_id", (me["id"],))}
    favoritos = c.execute(LOTQ + " WHERE id IN (SELECT lot_id FROM favorites WHERE user_id=?) ORDER BY status='open' DESC, end_ts", (me["id"],)).fetchall()
    leiloes = c.execute(AUCQ + " WHERE seller_id=? ORDER BY end_ts DESC", (me["id"],)).fetchall()
    habs = c.execute(AUCQ + " WHERE id IN (SELECT auction_id FROM habilitations WHERE user_id=?) ORDER BY end_ts DESC", (me["id"],)).fetchall()
    return render_template("account.html", minhas_notas=notas_of_user(me["id"]), meus_lotes=meus_lotes, lances=lances, my_max=my_max, favoritos=favoritos,
                           leiloes=leiloes, habs=habs, favs=my_favs(), aba=request.args.get("aba") or "lances")

@app.route("/cadastro", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form; name = (f.get("nome") or "").strip()[:80]; email = (f.get("email") or "").strip().lower()[:120]
        pw = f.get("senha") or ""; phone = ""
        err = None
        if len(name) < 2: err = "Informe seu nome."
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email): err = "E-mail inválido."
        elif len(pw) < 6: err = "A senha precisa ter pelo menos 6 caracteres."
        elif not f.get("aceite"): err = "Para criar a conta, aceite as Condições de uso e a Política de privacidade."
        elif db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone(): err = "Este e-mail já está cadastrado."
        if err:
            flash(err, "err"); return render_template("auth.html", mode="cadastro", f=f), 400
        cur = db().execute("INSERT INTO users(name,email,pw_hash,phone,created_at) VALUES(?,?,?,?,?)",
                           (name, email, generate_password_hash(pw), phone, now()))
        login_session(cur.lastrowid)
        u = db().execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        sent = send_confirmation(u)
        flash(f"Bem-vindo(a), {short_name(name)}! Conta criada. " + (f"Enviamos um link de confirmação para {email}." if sent else
              "Sua confirmação de e-mail está pendente."), "ok")
        return redirect(url_for("email_pending"))
    return render_template("auth.html", mode="cadastro", f={})

def safe_next(nxt):
    nxt = nxt or ""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else url_for("index")

@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:120]
        if is_locked(email):
            log_event("blocked", email)
            flash(LOCK_MSG, "err"); return render_template("auth.html", mode="entrar", f=request.form), 429
        u = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not u or not check_password_hash(u["pw_hash"], request.form.get("senha") or ""):
            register_fail(email)
            if is_locked(email):
                flash(LOCK_MSG, "err"); return render_template("auth.html", mode="entrar", f=request.form), 429
            flash(FAIL_MSG, "err"); return render_template("auth.html", mode="entrar", f=request.form), 401
        if u["totp_enabled"]:
            tok = session.get("csrf"); session.clear(); session["csrf"] = tok or secrets.token_hex(16)
            session["pre2fa"] = u["id"]; session["pre2fa_ts"] = now(); session["pre2fa_next"] = request.args.get("next") or ""
            return redirect(url_for("login_2fa"))
        log_event("ok", email); login_session(u["id"]); session["seen"] = now()
        return redirect(safe_next(request.args.get("next")))
    return render_template("auth.html", mode="entrar", f={})

def check_totp(u, code, c):
    """Valida código TOTP (janela ±30 s, sem reuso) ou código de recuperação (uso único)."""
    code = re.sub(r"\s|-", "", code or "")
    if u["totp_secret"] and code.isdigit() and len(code) == 6:
        totp = pyotp.TOTP(u["totp_secret"]); t = time.time()
        for off in (-1, 0, 1):
            step = int(t // 30) + off
            if step > (u["totp_last"] or 0) and secrets.compare_digest(totp.at(step * 30), code):
                c.execute("UPDATE users SET totp_last=? WHERE id=?", (step, u["id"])); return "totp"
        return None
    hashes = json.loads(u["recovery_hashes"] or "[]")
    for h in hashes:
        if code and check_password_hash(h, code.upper()):
            hashes.remove(h); c.execute("UPDATE users SET recovery_hashes=? WHERE id=?", (json.dumps(hashes), u["id"])); return "recovery"
    return None

@app.route("/entrar/codigo", methods=["GET", "POST"])
def login_2fa():
    uid = session.get("pre2fa")
    if not uid or now() - session.get("pre2fa_ts", 0) > 300:
        session.pop("pre2fa", None); flash("Entre novamente com e-mail e senha.", "warn"); return redirect(url_for("login"))
    c = db(); u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u: return redirect(url_for("login"))
    if request.method == "POST":
        if is_locked(u["email"]):
            log_event("blocked", u["email"]); session.pop("pre2fa", None)
            flash(LOCK_MSG, "err"); return redirect(url_for("login"))
        how = check_totp(u, request.form.get("codigo"), c)
        if not how:
            register_fail(u["email"], "fail2fa")
            flash("Código inválido. Confira o app autenticador e tente de novo.", "err")
            return render_template("auth.html", mode="codigo", f={}), 401
        nxt = session.get("pre2fa_next"); log_event("ok2fa" if how == "totp" else "ok_recovery", u["email"])
        login_session(u["id"]); session["seen"] = now()
        if how == "recovery":
            left = len(json.loads(c.execute("SELECT recovery_hashes FROM users WHERE id=?", (u["id"],)).fetchone()[0] or "[]"))
            flash(f"Você entrou com um código de recuperação. Restam {left}. Gere novos códigos em Admin › Segurança.", "warn")
        return redirect(safe_next(nxt))
    return render_template("auth.html", mode="codigo", f={})

def new_recovery_codes():
    codes = ["-".join(secrets.token_hex(2).upper() for _ in range(3)) for _ in range(8)]
    return codes, json.dumps([generate_password_hash(x.replace("-", "")) for x in codes])

@app.route("/admin/seguranca", methods=["GET", "POST"])
@admin_required
def admin_security():
    c = db(); me = current_user(); codes = None; qr = None; secret = None
    act = request.form.get("acao") if request.method == "POST" else None
    if act == "iniciar2fa" and not me["totp_enabled"]:
        session["totp_pending"] = pyotp.random_base32()
    elif act == "confirmar2fa" and not me["totp_enabled"] and session.get("totp_pending"):
        code = re.sub(r"\D", "", request.form.get("codigo") or "")
        if pyotp.TOTP(session["totp_pending"]).verify(code, valid_window=1):
            codes, hashes = new_recovery_codes()
            c.execute("UPDATE users SET totp_secret=?, totp_enabled=1, totp_last=?, recovery_hashes=? WHERE id=?",
                      (session.pop("totp_pending"), int(time.time() // 30), hashes, me["id"]))
            log_event("2fa_on", me["email"]); flash("Confirmação em dois passos ATIVADA.", "ok")
        else:
            flash("Código incorreto. Confira se o horário do celular está automático e tente de novo.", "err")
    elif act in ("desativar2fa", "novoscodigos") and me["totp_enabled"]:
        if not check_password_hash(me["pw_hash"], request.form.get("senha") or "") or not check_totp(me, request.form.get("codigo"), c):
            log_event("fail2fa", me["email"]); flash("Senha ou código incorretos.", "err")
        elif act == "desativar2fa":
            c.execute("UPDATE users SET totp_secret=NULL, totp_enabled=0, recovery_hashes='[]' WHERE id=?", (me["id"],))
            log_event("2fa_off", me["email"]); flash("Confirmação em dois passos desativada.", "warn")
        else:
            codes, hashes = new_recovery_codes()
            c.execute("UPDATE users SET recovery_hashes=? WHERE id=?", (hashes, me["id"])); log_event("2fa_codes", me["email"])
    elif act == "backup":
        try: name = make_backup("manual"); flash(f"Backup gerado: {name}", "ok")
        except Exception as e: flash(f"Falha ao gerar backup: {e}", "err")
        return redirect(url_for("admin_security") + "#backups")
    elif act == "desbloquear":
        c.execute("UPDATE login_log SET until_ts=? WHERE kind='lock' AND until_ts>?", (now(), now())); log_event("unlock_all", me["email"])
        flash("Bloqueios de login removidos.", "ok")
    me = c.execute("SELECT * FROM users WHERE id=?", (me["id"],)).fetchone()
    if not me["totp_enabled"] and session.get("totp_pending"):
        secret = session["totp_pending"]
        uri = pyotp.TOTP(secret).provisioning_uri(name=me["email"], issuer_name=get_cfg().get("empresa", "SP Leilões"))
        qr = Markup(segno.make(uri, error="m").svg_inline(scale=5, border=2))
    logs = c.execute("SELECT * FROM login_log ORDER BY id DESC LIMIT 200").fetchall()
    locks = c.execute("SELECT * FROM login_log WHERE kind='lock' AND until_ts>? ORDER BY id DESC", (now(),)).fetchall()
    return render_template("security.html", me2=me, codes=codes, qr=qr, secret=secret, logs=logs, locks=locks,
                           backups=list_backups(), left=len(json.loads(me["recovery_hashes"] or "[]")))

# ---------- backups ----------
BK_RE = re.compile(r"^sp-leiloes-\d{8}-\d{6}-(auto|manual)\.zip$")

def list_backups():
    out = []
    for fn in sorted(os.listdir(BACKUPS), reverse=True):
        if BK_RE.match(fn):
            st = os.stat(os.path.join(BACKUPS, fn)); out.append(dict(name=fn, size=st.st_size, ts=int(st.st_mtime)))
    return out

_bk_lock = threading.Lock()
def make_backup(kind="auto"):
    with _bk_lock:
        stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        name = f"sp-leiloes-{stamp}-{kind}.zip"; final = os.path.join(BACKUPS, name)
        fd, tmpdb = tempfile.mkstemp(suffix=".db", dir=BACKUPS); os.close(fd)
        try:
            src = sqlite3.connect(DB_PATH, timeout=30); dst = sqlite3.connect(tmpdb)
            with dst: src.backup(dst)
            src.close(); dst.close()
            tmpzip = final + ".part"
            with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmpdb, "leiloes.db")
                for fn in os.listdir(UPLOADS):
                    fp = os.path.join(UPLOADS, fn)
                    if os.path.isfile(fp): z.write(fp, "uploads/" + fn, compress_type=zipfile.ZIP_STORED)
            os.replace(tmpzip, final)
        finally:
            try: os.remove(tmpdb)
            except OSError: pass
        for old in list_backups()[KEEP_BACKUPS:]:
            try: os.remove(os.path.join(BACKUPS, old["name"]))
            except OSError: pass
        return name

def backup_loop():
    time.sleep(60)
    while True:
        try:
            bks = list_backups()
            if not bks or time.time() - bks[0]["ts"] > 24 * 3600:
                print("backup:", make_backup("auto"), flush=True)
        except Exception as e:
            print("backup erro:", e, flush=True)
        time.sleep(3600)

@app.get("/admin/backup/<name>")
@admin_required
def download_backup(name):
    if name == "ultimo":
        bks = list_backups()
        if not bks: flash("Ainda não há backup. Gere um agora.", "warn"); return redirect(url_for("admin_security") + "#backups")
        name = bks[0]["name"]
    if not BK_RE.match(name) or not os.path.exists(os.path.join(BACKUPS, name)): abort(404)
    log_event("backup_download", current_user()["email"])
    return send_from_directory(BACKUPS, name, as_attachment=True, max_age=0)

@app.get("/sair")
def logout():
    session.clear(); return redirect(url_for("index"))

# ---------- v0.8: confirmação de e-mail, ficha do cliente, clientes (admin) ----------
UFS = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
DDDS = {11,12,13,14,15,16,17,18,19,21,22,24,27,28,31,32,33,34,35,37,38,41,42,43,44,45,46,47,48,49,51,53,54,55,
        61,62,63,64,65,66,67,68,69,71,73,74,75,77,79,81,82,83,84,85,86,87,88,89,91,92,93,94,95,96,97,98,99}
TOKEN_TTL = 24 * 3600

def only_digits(s): return re.sub(r"\D", "", s or "")

def cpf_valid(s):
    d = only_digits(s)
    if len(d) != 11 or d == d[0] * 11: return None
    for n in (9, 10):
        v = sum(int(d[i]) * (n + 1 - i) for i in range(n)) * 10 % 11 % 10
        if v != int(d[n]): return None
    return d

def phone_valid(s):
    d = only_digits(s)
    if len(d) in (12, 13) and d.startswith("55"): d = d[2:]
    if d.startswith("0"): d = d[1:]
    if len(d) not in (10, 11) or int(d[:2]) not in DDDS: return None
    if len(d) == 11 and d[2] != "9": return None
    if len(d) == 10 and d[2] not in "2345": return None
    return d

def fmt_cpf(d): d = only_digits(d); return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else (d or "")
def fmt_phone(d):
    d = only_digits(d)
    if len(d) == 11: return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10: return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return d or ""
def wa_url(d, text=""):
    d = phone_valid(d)
    return ("https://wa.me/55" + d + (("?" + urlencode({"text": text})) if text else "")) if d else ""
def fmt_cep(d): d = only_digits(d); return f"{d[:5]}-{d[5:]}" if len(d) == 8 else (d or "")
def fmt_birth(s):
    try: return datetime.strptime(s, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception: return s or ""
def age_of(s):
    try: b = datetime.strptime(s, "%Y-%m-%d").date()
    except Exception: return None
    t = datetime.now(TZ).date()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))

app.jinja_env.filters.update(cpf=fmt_cpf, phone=fmt_phone, wa=wa_url, cep=fmt_cep, birth=fmt_birth, age=age_of)

def ficha_ok(u):
    return bool(u and u["profile_at"] and u["cpf"] and u["birth"] and u["cep"] and u["street"] and u["number"]
                and u["district"] and u["city"] and u["uf"] and phone_valid(u["phone"]))

def gate(me, nxt=None):
    """None se o usuário pode se habilitar/dar lances; senão (mensagem, url)."""
    if not me or me["is_admin"]: return None
    if not me["email_verified"]:
        return ("Confirme seu e-mail para se habilitar e dar lances.", url_for("email_pending"))
    if not ficha_ok(me):
        return ("Complete sua ficha de cliente (CPF, WhatsApp, nascimento e endereço) para se habilitar e dar lances.",
                url_for("ficha", next=nxt or request.path))
    return None

# ----- e-mail -----
def mail_mode():
    # Brevo (API HTTP) tem prioridade: o Railway bloqueia SMTP de saída
    if os.environ.get("BREVO_API_KEY"): return "brevo"
    if os.environ.get("SMTP_HOST") and (os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")): return "smtp"
    if os.environ.get("RESEND_API_KEY") and os.environ.get("SMTP_FROM"): return "resend"
    return None

def mail_log(to, kind, ok, info):
    db().execute("INSERT INTO email_log(ts,to_email,kind,ok,info) VALUES(?,?,?,?,?)", (now(), to[:120], kind, 1 if ok else 0, (info or "")[:300]))

def send_mail(to, subject, text, html, kind="geral", attachments=None):
    import smtplib, urllib.request, urllib.error
    from email.message import EmailMessage
    from email.utils import parseaddr, formataddr
    mode = mail_mode()
    if not mode: mail_log(to, kind, False, "sem credencial de e-mail configurada"); return False
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER")
    sname, saddr = parseaddr(sender); sname = sname or get_cfg().get("empresa", "SP Leilões")
    try:
        if mode == "smtp":
            import socket
            m = EmailMessage(); m["Subject"] = subject; m["From"] = formataddr((sname, saddr)); m["To"] = to
            m.set_content(text); m.add_alternative(html, subtype="html")
            for an, ab in (attachments or []): m.add_attachment(ab, maintype="application", subtype="pdf", filename=an)
            port = int(os.environ.get("SMTP_PORT") or 587); host = os.environ["SMTP_HOST"]
            # Preferir IPv4 (Railway às vezes falha com "Network is unreachable" no IPv6)
            try:
                ip = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
            except Exception:
                ip = host
            s = smtplib.SMTP_SSL(ip, port, timeout=20) if port == 465 else smtplib.SMTP(ip, port, timeout=20)
            with s:
                s._host = host  # SNI / certificado com o nome certo
                if port != 465: s.starttls()
                if os.environ.get("SMTP_USER"): s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
                s.send_message(m)
        else:
            if mode == "brevo":
                url, hdr = "https://api.brevo.com/v3/smtp/email", {"api-key": os.environ["BREVO_API_KEY"]}
                bname = os.environ.get("BREVO_SENDER_NAME") or "SP Leilões"
                baddr = os.environ.get("BREVO_SENDER_EMAIL") or "splsp846@gmail.com"
                body = {"sender": {"name": bname, "email": baddr}, "to": [{"email": to}], "subject": subject, "htmlContent": html, "textContent": text}
                if attachments: body["attachment"] = [{"name": an, "content": base64.b64encode(ab).decode()} for an, ab in attachments]
            else:
                url, hdr = "https://api.resend.com/emails", {"Authorization": "Bearer " + os.environ["RESEND_API_KEY"]}
                body = {"from": formataddr((sname, saddr)), "to": [to], "subject": subject, "html": html, "text": text}
                if attachments: body["attachments"] = [{"filename": an, "content": base64.b64encode(ab).decode()} for an, ab in attachments]
            req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={**hdr, "Content-Type": "application/json", "Accept": "application/json"})
            try:
                resp = urllib.request.urlopen(req, timeout=20).read()
            except urllib.error.HTTPError as he:
                # corpo do erro do provedor (sem a chave) para diagnóstico
                detail = he.read().decode("utf-8", "replace")[:200]
                raise RuntimeError(f"HTTP {he.code}: {detail}") from None
            try: mid = json.loads(resp or b"{}").get("messageId") or json.loads(resp or b"{}").get("id") or ""
            except Exception: mid = ""
            print("email ok:", mode, kind, mid, flush=True)
            mail_log(to, kind, True, f"{mode} {mid}".strip()); return True
        mail_log(to, kind, True, mode); return True
    except Exception as e:
        print("email erro:", mode, type(e).__name__, str(e)[:200], flush=True)
        mail_log(to, kind, False, f"{mode}: {type(e).__name__}: {str(e)[:200]}"); return False

WA_PATH = "M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.17-.17.2-.35.22-.64.07-.3-.15-1.26-.46-2.4-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.6.13-.13.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.2-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.79.37-.27.3-1.04 1.02-1.04 2.48s1.07 2.88 1.21 3.08c.15.2 2.1 3.2 5.08 4.49.71.31 1.26.49 1.69.63.71.23 1.36.2 1.87.12.57-.09 1.76-.72 2.01-1.41.25-.69.25-1.29.17-1.41-.07-.12-.27-.2-.57-.35M12.05 21.5h-.01a9.4 9.4 0 0 1-4.8-1.32l-.34-.2-3.57.94.95-3.48-.22-.36a9.4 9.4 0 0 1-1.44-5.02c0-5.2 4.24-9.43 9.44-9.43 2.52 0 4.89.98 6.67 2.77a9.37 9.37 0 0 1 2.76 6.67c0 5.2-4.24 9.43-9.44 9.43m8.03-17.46A11.27 11.27 0 0 0 12.05.72C5.8.72.7 5.8.7 12.07c0 2 .52 3.95 1.52 5.67L.6 23.62l6.02-1.58a11.33 11.33 0 0 0 5.42 1.38h.01c6.26 0 11.35-5.09 11.35-11.35 0-3.03-1.18-5.88-3.32-8.03"

def wa_svg(size=18, fill="currentColor"):
    return Markup(f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{fill}" aria-hidden="true"><path d="{WA_PATH}"/></svg>')

def lot_share(l, cur=None):
    """Texto e link de compartilhamento (WhatsApp) de um lote."""
    keys = l.keys()
    if cur is None: cur = l["cur"] if "cur" in keys else None
    if l["status"] != "open" and l["final_cents"]:
        lbl, val = "Arrematado por", l["final_cents"]
    else:
        lbl, val = ("Lance atual", cur) if cur else ("Lance inicial", l["start_cents"])
    url = f"{base_url()}/lote/{l['id']}"
    when = ("Encerra em " if l["status"] == "open" else "Encerrado em ") + fmt_dmy(l["end_ts"] if l["status"] == "open" else (l["closed_at"] or l["end_ts"]))
    if "auction_start" in keys and l["auction_start"] and l["auction_start"] > now():
        when = "Abre para lances em " + fmt_dmy(l["auction_start"]) + " · " + when
    text = f"{l['title']} — Lote {lotno(l)}\n{lbl}: {brl(val)}\n{when}\n{url}"
    return dict(url=url, text=text, wa="https://wa.me/?text=" + quote(text, safe=""), title=f"{l['title']} — Lote {lotno(l)}",
                desc=f"{lbl}: {brl(val)} · {when}")

def base_url():
    if os.environ.get("PUBLIC_URL"): return os.environ["PUBLIC_URL"].rstrip("/")
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN"): return "https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]
    return request.url_root.rstrip("/")

def send_confirmation(u):
    """Gera token (24 h) e envia o link. Retorna True se o e-mail saiu."""
    import hashlib
    c = db(); tok = secrets.token_urlsafe(32)
    c.execute("DELETE FROM email_tokens WHERE user_id=? AND used_at IS NULL", (u["id"],))
    c.execute("INSERT INTO email_tokens(user_id,token_hash,expires_ts,created_at) VALUES(?,?,?,?)",
              (u["id"], hashlib.sha256(tok.encode()).hexdigest(), now() + TOKEN_TTL, now()))
    c.execute("UPDATE users SET verify_sent_at=? WHERE id=?", (now(), u["id"]))
    link = base_url() + url_for("email_confirm", token=tok); emp = get_cfg().get("empresa", "SP Leilões")
    text = (f"Olá, {short_name(u['name'])}!\n\nConfirme seu e-mail para participar dos leilões da {emp}:\n{link}\n\n"
            "O link vale por 24 horas. Se você não criou esta conta, ignore esta mensagem.")
    html = (f"<div style='font-family:Arial,sans-serif;max-width:520px'><h2 style='color:#b4141c'>{emp}</h2><p>Olá, {Markup.escape(short_name(u['name']))}!</p>"
            f"<p>Confirme seu e-mail para se habilitar e dar lances nos leilões.</p><p><a href='{link}' style='display:inline-block;background:#d42a32;color:#fff;"
            f"padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:bold'>Confirmar meu e-mail</a></p>"
            f"<p style='color:#666;font-size:13px'>Ou copie o link: {link}<br>O link vale por 24 horas. Se você não criou esta conta, ignore esta mensagem.</p></div>")
    return send_mail(u["email"], f"Confirme seu e-mail — {emp}", text, html, "confirmacao")

@app.get("/confirmar-email")
@login_required
def email_pending():
    me = current_user()
    return render_template("email_pending.html", mode=mail_mode(), ficha_ok=ficha_ok(me))

@app.post("/confirmar-email/reenviar")
@login_required
def email_resend():
    me = current_user(); c = db()
    if me["email_verified"]: return redirect(url_for("account"))
    if not mail_mode():
        flash("O envio automático de e-mails ainda não está ativo. A equipe vai confirmar seu cadastro manualmente.", "warn")
    elif now() - (me["verify_sent_at"] or 0) < 60:
        flash("Aguarde 1 minuto para pedir um novo e-mail.", "warn")
    elif c.execute("SELECT COUNT(*) FROM email_log WHERE to_email=? AND kind='confirmacao' AND ts>?", (me["email"], now() - 86400)).fetchone()[0] >= 6:
        flash("Limite de reenvios atingido hoje. Fale com o atendimento.", "err")
    elif send_confirmation(me):
        flash(f"Enviamos um novo link para {me['email']}. Confira também o spam.", "ok")
    else:
        flash("Não conseguimos enviar o e-mail agora. Tente mais tarde ou fale com o atendimento.", "err")
    return redirect(url_for("email_pending"))

@app.get("/confirmar-email/<token>")
def email_confirm(token):
    import hashlib
    c = db(); h = hashlib.sha256((token or "").encode()).hexdigest()
    t = c.execute("SELECT * FROM email_tokens WHERE token_hash=?", (h,)).fetchone()
    if not t or t["used_at"]:
        return render_template("msg.html", title="Link inválido", text="Este link de confirmação é inválido ou já foi usado. Entre na sua conta para pedir um novo."), 400
    if t["expires_ts"] < now():
        return render_template("msg.html", title="Link expirado", text="Este link expirou (vale 24 horas). Entre na sua conta e clique em “Reenviar e-mail”."), 400
    c.execute("UPDATE email_tokens SET used_at=? WHERE id=?", (now(), t["id"]))
    c.execute("UPDATE users SET email_verified=1, email_verified_at=?, email_verified_by='link' WHERE id=?", (now(), t["user_id"]))
    flash("E-mail confirmado! ✅", "ok")
    me = current_user()
    if me and me["id"] == t["user_id"]:
        g.pop("user", None); me = current_user()
        return redirect(url_for("index") if ficha_ok(me) else url_for("ficha"))
    return redirect(url_for("login"))

@app.route("/minha-conta/ficha", methods=["GET", "POST"])
@login_required
def ficha():
    me = current_user(); c = db(); nxt = request.values.get("next") or ""
    if request.method == "POST":
        f = request.form; err = None
        name = re.sub(r"\s+", " ", f.get("nome") or "").strip()[:120]
        cpf = cpf_valid(f.get("cpf")); phone = phone_valid(f.get("whatsapp")); birth = (f.get("nascimento") or "").strip()
        cep = only_digits(f.get("cep")); uf = (f.get("uf") or "").strip().upper()
        street, number = (f.get("rua") or "").strip()[:150], (f.get("numero") or "").strip()[:20]
        compl, district, city = (f.get("complemento") or "").strip()[:80], (f.get("bairro") or "").strip()[:80], (f.get("cidade") or "").strip()[:80]
        age = age_of(birth); rg = re.sub(r"[^0-9A-Za-z./\- ]", "", f.get("rg") or "").strip()[:25]
        if len(name.split()) < 2 or len(name) < 5: err = "Informe seu nome completo (nome e sobrenome)."
        elif not cpf: err = "CPF inválido. Confira os números."
        elif c.execute("SELECT 1 FROM users WHERE cpf=? AND id<>?", (cpf, me["id"])).fetchone(): err = "Este CPF já está cadastrado em outra conta. Fale com o atendimento."
        elif not phone: err = "WhatsApp inválido. Informe com DDD, ex.: (11) 98765-4321."
        elif age is None or age > 120: err = "Data de nascimento inválida."
        elif age < 18: err = "É preciso ter 18 anos ou mais para participar dos leilões."
        elif len(cep) != 8: err = "CEP inválido (8 números)."
        elif len(street) < 3 or not number: err = "Informe rua e número (use S/N se não houver número)."
        elif len(district) < 2 or len(city) < 2: err = "Informe bairro e cidade."
        elif uf not in UFS: err = "Selecione o estado (UF)."
        if err:
            flash(err, "err"); return render_template("ficha.html", f=f, nxt=nxt, UFS=UFS), 400
        c.execute("""UPDATE users SET name=?, cpf=?, phone=?, birth=?, cep=?, street=?, number=?, complement=?, district=?, city=?, uf=?, profile_at=?, rg=?
                     WHERE id=?""", (name, cpf, phone, birth, cep, street, number, compl, district, city, uf, now(), rg, me["id"]))
        g.pop("user", None); me = current_user()
        flash("Ficha salva! ✅", "ok")
        gt = gate(me, nxt)
        if gt: flash(gt[0], "warn"); return redirect(gt[1])
        return redirect(safe_next(nxt) if nxt else url_for("account"))
    f = {"nome": me["name"], "cpf": fmt_cpf(me["cpf"]), "whatsapp": fmt_phone(me["phone"]) if phone_valid(me["phone"]) else (me["phone"] or ""),
         "nascimento": me["birth"] or "", "cep": fmt_cep(me["cep"]), "rua": me["street"] or "", "numero": me["number"] or "",
         "complemento": me["complement"] or "", "bairro": me["district"] or "", "cidade": me["city"] or "", "uf": me["uf"] or "", "rg": me["rg"] or ""}
    return render_template("ficha.html", f=f, nxt=nxt, UFS=UFS)

# ----- admin: clientes -----
CLIQ = """SELECT u.*, (SELECT COUNT(*) FROM habilitations h WHERE h.user_id=u.id) nhab,
  (SELECT COUNT(*) FROM bids b WHERE b.user_id=u.id) nbids,
  (SELECT COUNT(*) FROM lots l WHERE l.winner_id=u.id AND l.status='closed') nwins FROM users u"""

def clientes_query(args):
    q = (args.get("q") or "").strip()[:80]; filt = args.get("f") or "todos"
    where, p = ["u.is_admin=0"], []
    if q:
        d = only_digits(q); like = f"%{q.lower()}%"
        cond = "(lower(u.name) LIKE ? OR lower(u.email) LIKE ? OR lower(COALESCE(u.city,'')) LIKE ?"; p += [like, like, like]
        if len(d) >= 3: cond += " OR u.cpf LIKE ? OR u.phone LIKE ?"; p += [f"%{d}%", f"%{d}%"]
        where.append(cond + ")")
    if filt == "pendentes": where.append("COALESCE(u.email_verified,0)=0")
    elif filt == "sem_ficha": where.append("u.profile_at IS NULL")
    elif filt == "completos": where.append("u.email_verified=1 AND u.profile_at IS NOT NULL")
    return db().execute(CLIQ + " WHERE " + " AND ".join(where) + " ORDER BY u.id DESC", p).fetchall(), q, filt

@app.get("/admin/clientes")
@admin_required
def admin_clients():
    rows, q, filt = clientes_query(request.args); c = db()
    stats = c.execute("""SELECT COUNT(*) total, SUM(COALESCE(email_verified,0)=0) pend, SUM(profile_at IS NULL) semficha
                         FROM users WHERE is_admin=0""").fetchone()
    mails = c.execute("SELECT * FROM email_log ORDER BY id DESC LIMIT 15").fetchall()
    return render_template("clients.html", rows=rows, q=q, filt=filt, stats=stats, mode=mail_mode(), mails=mails, ficha_ok=ficha_ok)

@app.get("/admin/clientes.csv")
@admin_required
def admin_clients_csv():
    import csv
    rows, _, _ = clientes_query(request.args)
    def cell(v):
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    out = io.StringIO(); w = csv.writer(out, delimiter=";")
    w.writerow(["id", "nome", "email", "email_confirmado", "cpf", "whatsapp", "nascimento", "cep", "rua", "numero", "complemento",
                "bairro", "cidade", "uf", "cadastro", "ficha_atualizada", "habilitacoes", "lances", "arremates"])
    for u in rows:
        w.writerow([cell(x) for x in [u["id"], u["name"], u["email"], "sim" if u["email_verified"] else "não", fmt_cpf(u["cpf"]),
                    fmt_phone(u["phone"]), fmt_birth(u["birth"]), fmt_cep(u["cep"]), u["street"], u["number"], u["complement"],
                    u["district"], u["city"], u["uf"], fmt_dt(u["created_at"]), fmt_dt(u["profile_at"]), u["nhab"], u["nbids"], u["nwins"]]])
    log_event("export_csv", current_user()["email"])
    name = "clientes-" + datetime.now(TZ).strftime("%Y%m%d-%H%M") + ".csv"
    return app.response_class("\ufeff" + out.getvalue(), content_type="text/csv; charset=utf-8",
                              headers={"Content-Disposition": f"attachment; filename={name}", "Cache-Control": "no-store"})

@app.route("/admin/cliente/<int:uid>", methods=["GET", "POST"])
@admin_required
def admin_client(uid):
    c = db(); u = c.execute(CLIQ + " WHERE u.id=?", (uid,)).fetchone()
    if not u: abort(404)
    if request.method == "POST":
        act = request.form.get("acao")
        if act == "confirmar" and not u["email_verified"]:
            c.execute("UPDATE users SET email_verified=1, email_verified_at=?, email_verified_by='admin' WHERE id=?", (now(), uid))
            log_event("email_admin", u["email"]); flash("E-mail confirmado manualmente.", "ok")
        elif act == "reenviar" and not u["email_verified"]:
            flash("E-mail de confirmação reenviado." if send_confirmation(u) else
                  ("Envio de e-mail não configurado — confirme manualmente." if not mail_mode() else "Falha ao enviar o e-mail (veja o log em Clientes)."),
                  "ok" if mail_mode() else "warn")
        return redirect(url_for("admin_client", uid=uid))
    habs = c.execute("SELECT a.id, a.title, a.end_ts, h.created_at FROM habilitations h JOIN auctions a ON a.id=h.auction_id WHERE h.user_id=? ORDER BY h.created_at DESC", (uid,)).fetchall()
    bids = c.execute("""SELECT b.amount_cents, b.created_at, l.id lid, l.title, l.status, l.winner_id FROM bids b JOIN lots l ON l.id=b.lot_id
                        WHERE b.user_id=? ORDER BY b.id DESC LIMIT 200""", (uid,)).fetchall()
    wins = c.execute("SELECT id, title, final_cents, closed_at, commission_pct FROM lots WHERE winner_id=? AND status='closed' ORDER BY closed_at DESC", (uid,)).fetchall()
    return render_template("client.html", notas=notas_of_user(uid), u=u, habs=habs, bids=bids, wins=wins, ok=ficha_ok(u), mode=mail_mode())

@app.get("/como-funciona")
def how(): return render_template("page.html", page="como")
@app.get("/condicoes")
def terms(): return render_template("page.html", page="condicoes")
@app.get("/contato")
def contact(): return render_template("page.html", page="contato")

@app.get("/admin")
@admin_required
def admin():
    c = db()
    users = c.execute("""SELECT u.*, (SELECT COUNT(*) FROM lots WHERE seller_id=u.id) nl,
                         (SELECT COUNT(*) FROM bids WHERE user_id=u.id) nb FROM users u ORDER BY u.id DESC""").fetchall()
    winners = c.execute("""SELECT l.id, l.title, l.final_cents, l.closed_at, u.id uid, u.name, u.email, u.phone FROM lots l
                           JOIN users u ON u.id=l.winner_id WHERE l.status='closed' ORDER BY l.closed_at DESC""").fetchall()
    return render_template("admin.html", users=users, winners=winners, lots=c.execute(LOTQ + " ORDER BY id DESC").fetchall(),
                           auctions=c.execute(AUCQ + " ORDER BY id DESC").fetchall())

@app.route("/admin/configuracoes", methods=["GET", "POST"])
@admin_required
def admin_settings():
    c = db()
    if request.method == "POST":
        for k, _ in SETTINGS:
            if k.startswith("_"): continue
            v = (request.form.get(k) or "").strip()[:300]
            c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        flash("Dados da empresa salvos. Campos vazios ficam ocultos no site.", "ok")
        return redirect(url_for("admin_settings"))
    vals = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM settings")}
    return render_template("settings.html", fields=SETTINGS, vals=vals)

@app.post("/admin/usuario/<int:uid>/remover")
@admin_required
def admin_del_user(uid):
    c = db()
    u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u or u["is_admin"]:
        flash("Não é possível remover este usuário.", "err"); return redirect(url_for("admin"))
    for l in c.execute("SELECT id FROM lots WHERE seller_id=?", (uid,)).fetchall(): remove_lot(c, l["id"])
    for a in c.execute("SELECT id FROM auctions WHERE seller_id=?", (uid,)).fetchall():
        c.execute("UPDATE lots SET auction_id=NULL WHERE auction_id=?", (a["id"],)); c.execute("DELETE FROM auctions WHERE id=?", (a["id"],))
    c.execute("DELETE FROM bids WHERE user_id=?", (uid,))
    c.execute("DELETE FROM favorites WHERE user_id=?", (uid,))
    c.execute("DELETE FROM habilitations WHERE user_id=?", (uid,))
    c.execute("DELETE FROM email_tokens WHERE user_id=?", (uid,))
    c.execute("UPDATE lots SET winner_id=NULL WHERE winner_id=?", (uid,))
    c.execute("DELETE FROM users WHERE id=?", (uid,))
    flash(f"Usuário {u['email']} removido (com seus leilões, lotes e lances).", "ok")
    nxt = request.form.get("next")
    return redirect(safe_next(nxt) if nxt else url_for("admin"))

# ----- nova senha (recuperação por e-mail) -----
PW_TTL = 3600
PW_MSG = "Se houver uma conta com este e-mail, enviamos um link para criar uma nova senha. O link vale por 1 hora — confira também a caixa de spam."

def pw_token_row(token):
    import hashlib
    return db().execute("SELECT t.*, u.email, u.name FROM pw_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.used_at IS NULL AND t.expires_ts>?",
                        (hashlib.sha256((token or "").encode()).hexdigest(), now())).fetchone()

@app.route("/nova-senha", methods=["GET", "POST"])
def password_forgot():
    if request.method == "POST":
        import hashlib
        c = db(); email = (request.form.get("email") or "").strip().lower()[:120]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Informe um e-mail válido.", "err"); return render_template("password.html", step="pedir", email=email), 400
        n_ip = c.execute("SELECT COUNT(*) FROM login_log WHERE kind='pwreq' AND ip=? AND ts>?", (client_ip(), now() - 3600)).fetchone()[0]
        if n_ip >= 5:
            flash("Muitas solicitações. Aguarde alguns minutos e tente de novo.", "err"); return render_template("password.html", step="pedir", email=email), 429
        log_event("pwreq", email)
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if u and mail_mode():
            last = c.execute("SELECT MAX(created_at) FROM pw_tokens WHERE user_id=?", (u["id"],)).fetchone()[0] or 0
            nday = c.execute("SELECT COUNT(*) FROM pw_tokens WHERE user_id=? AND created_at>?", (u["id"], now() - 86400)).fetchone()[0]
            if now() - last >= 60 and nday < 6:
                tok = secrets.token_urlsafe(32)
                c.execute("DELETE FROM pw_tokens WHERE user_id=? AND used_at IS NULL", (u["id"],))
                c.execute("INSERT INTO pw_tokens(user_id,token_hash,expires_ts,created_at) VALUES(?,?,?,?)",
                          (u["id"], hashlib.sha256(tok.encode()).hexdigest(), now() + PW_TTL, now()))
                link = base_url() + url_for("password_reset", token=tok); emp = get_cfg().get("empresa", "SP Leilões")
                text = (f"Olá, {short_name(u['name'])}!\n\nRecebemos um pedido para criar uma nova senha na {emp}.\n"
                        f"Para continuar, acesse:\n{link}\n\nO link vale por 1 hora e só pode ser usado uma vez. "
                        "Se você não fez este pedido, ignore esta mensagem: sua senha atual continua valendo.")
                html = (f"<div style='font-family:Arial,sans-serif;max-width:520px'><h2 style='color:#b4141c'>{emp}</h2><p>Olá, {Markup.escape(short_name(u['name']))}!</p>"
                        f"<p>Recebemos um pedido para criar uma nova senha.</p><p><a href='{link}' style='display:inline-block;background:#b4141c;color:#fff;"
                        f"padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:bold'>Criar nova senha</a></p>"
                        f"<p style='color:#666;font-size:13px'>Ou copie o link: {link}<br>O link vale por 1 hora e só pode ser usado uma vez. "
                        "Se você não fez este pedido, ignore esta mensagem: sua senha atual continua valendo.</p></div>")
                send_mail(u["email"], f"Nova senha — {emp}", text, html, "senha")
        flash(PW_MSG, "ok")
        return redirect(url_for("login"))
    return render_template("password.html", step="pedir", email="")

@app.route("/nova-senha/<token>", methods=["GET", "POST"])
def password_reset(token):
    row = pw_token_row(token)
    if not row:
        flash("Este link de nova senha é inválido, já foi usado ou expirou. Peça um novo.", "err")
        return redirect(url_for("password_forgot"))
    if request.method == "POST":
        pw = request.form.get("senha") or ""
        if len(pw) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.", "err"); return render_template("password.html", step="nova", row=row), 400
        if pw != (request.form.get("senha2") or ""):
            flash("As duas senhas não conferem.", "err"); return render_template("password.html", step="nova", row=row), 400
        c = db()
        c.execute("UPDATE users SET pw_hash=?, email_verified=1, email_verified_at=COALESCE(email_verified_at,?), email_verified_by=COALESCE(email_verified_by,'senha') WHERE id=?",
                  (generate_password_hash(pw), now(), row["user_id"]))
        c.execute("UPDATE pw_tokens SET used_at=? WHERE id=?", (now(), row["id"]))
        c.execute("DELETE FROM pw_tokens WHERE user_id=? AND used_at IS NULL", (row["user_id"],))
        log_event("pwreset", row["email"])
        flash("Senha alterada! Entre com a nova senha.", "ok")
        return redirect(url_for("login"))
    return render_template("password.html", step="nova", row=row)

# ----- admin: página inicial (banners e fotos) -----
SITE_IMGS = [("hero", "Fundo do topo (página inicial)")] + [(CAT_SLUG[c], f"Categoria: {c}") for c in CATEGORIAS]

def safe_link(v):
    v = (v or "").strip()[:300]
    if not v: return ""
    if v.startswith("/") and not v.startswith("//"): return v
    if re.match(r"https?://[^\s]+$", v, re.I): return v
    return None

def save_one_image(field):
    fl = request.files.get(field)
    if not fl or not fl.filename: return None, "Escolha uma imagem."
    out, err = process_image(fl.stream.read(MAX_PHOTO + 1), (fl.filename or "imagem")[-60:])
    if err: return None, err
    name = "site_" + secrets.token_hex(10) + ".jpg"
    with open(os.path.join(UPLOADS, name), "wb") as fh: fh.write(out)
    return name, None

def rm_upload(fn):
    if fn and not fn.startswith("ex_"):
        try: os.remove(os.path.join(UPLOADS, os.path.basename(fn)))
        except OSError: pass

@app.route("/admin/pagina-inicial", methods=["GET", "POST"])
@admin_required
def admin_home():
    c = db()
    if request.method == "POST":
        f = request.form; act = f.get("acao")
        bid = int(f["id"]) if (f.get("id") or "").isdigit() else None
        pos = int(f["ordem"]) if (f.get("ordem") or "").lstrip("-").isdigit() else 0
        fila = int(f["fila"]) if f.get("fila") in ("1", "2", "3") else 1
        if act in ("add_banner", "upd_banner"):
            link = safe_link(f.get("link"))
            if link is None:
                flash("Link inválido: use um endereço do site (ex.: /leilao/3) ou um link completo começando com https://", "err")
                return redirect(url_for("admin_home"))
            if act == "add_banner":
                name, err = save_one_image("imagem")
                if err: flash(err, "err"); return redirect(url_for("admin_home"))
                c.execute("INSERT INTO banners(filename,link,title,pos,active,created_at,fila) VALUES(?,?,?,?,1,?,?)",
                          (name, link, (f.get("titulo") or "").strip()[:120], pos, now(), fila))
                flash("Banner adicionado.", "ok")
            elif bid:
                c.execute("UPDATE banners SET link=?, title=?, pos=?, active=?, fila=? WHERE id=?",
                          (link, (f.get("titulo") or "").strip()[:120], pos, 1 if f.get("ativo") else 0, fila, bid))
                flash("Banner atualizado.", "ok")
        elif act == "del_banner" and bid:
            b = c.execute("SELECT filename FROM banners WHERE id=?", (bid,)).fetchone()
            if b: rm_upload(b["filename"]); c.execute("DELETE FROM banners WHERE id=?", (bid,)); flash("Banner removido.", "ok")
        elif act in ("img_set", "img_reset") and f.get("slug") in dict(SITE_IMGS):
            k = "img_" + f["slug"]; old = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
            if act == "img_set":
                name, err = save_one_image("imagem")
                if err: flash(err, "err"); return redirect(url_for("admin_home") + "#fotos")
                c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, name))
                flash("Foto atualizada.", "ok")
            else:
                c.execute("DELETE FROM settings WHERE k=?", (k,)); flash("Foto padrão restaurada.", "ok")
            if old: rm_upload(old["v"])
            return redirect(url_for("admin_home") + "#fotos")
        return redirect(url_for("admin_home"))
    return render_template("admin_home.html", banners=c.execute("SELECT * FROM banners ORDER BY fila, pos, id").fetchall(), imgs=SITE_IMGS)

# ================= Notas de arrematação =================
NOTA_STATUS = ["Pendente", "Paga", "Retirada", "Cancelada"]
MOD_NOTA = {"Online": "Leilão online (eletrônico)", "Presencial": "Leilão presencial", "Online e presencial": "Leilão online e presencial"}
NOTA_LEVAR = "RG e CPF originais (ou procuração com firma reconhecida)\nComprovante de pagamento\nEsta nota de arrematação"
NOTA_DOCS = ("Recibo de pagamento emitido pelo leiloeiro.\n"
             "Veículos: ATPV-e (antigo DUT) preenchido em nome do arrematante. A transferência no Detran deve ser feita em até 30 dias, sob pena de multa.\n"
             "Imóveis: carta / auto de arrematação, para registro no Cartório de Registro de Imóveis (ITBI e custas por conta do arrematante).\n"
             "Débitos (IPVA, multas, IPTU etc.): conforme o edital.")
NOTA_COND = ("Os bens são vendidos no estado de conservação em que se encontram, sem garantia. O arrematante declara ter tido oportunidade de examinar o bem antes do leilão.\n"
             "O edital do leilão prevalece sobre esta nota em caso de divergência. Ao dar o lance, o arrematante aceitou todas as condições do edital.")

def cfg_from(c):
    d = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM settings") if (r["v"] or "").strip()}
    d.setdefault("empresa", "SP Leilões"); return d

def money0(v):
    v = (v or "").strip()
    if v in ("", "0", "0,00", "0.00", "R$ 0,00"): return 0
    return parse_money(v)

def pct_of(v, dflt):
    try: return max(0.0, min(100.0, float(str(v).replace(",", ".").replace("%", "").strip())))
    except (TypeError, ValueError): return dflt

def site_host():
    u = os.environ.get("PUBLIC_URL") or (("https://" + os.environ["RAILWAY_PUBLIC_DOMAIN"]) if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else "")
    return re.sub(r"^https?://", "", u).rstrip("/")

def nota_defaults(c, lot_id, user_id):
    l = c.execute("SELECT l.*, a.id aid, a.end_ts a_end, a.modality, a.location FROM lots l LEFT JOIN auctions a ON a.id=l.auction_id WHERE l.id=?", (lot_id,)).fetchone()
    u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not l or not u: raise ValueError("lote ou cliente inexistente")
    cfg = cfg_from(c); razao = cfg.get("razao_social") or cfg["empresa"]
    when = l["a_end"] or l["end_ts"]
    end = " — ".join(x for x in [", ".join(x for x in [u["street"], u["number"]] if x), u["complement"], u["district"]] if x)
    end2 = " — ".join(x for x in [f"{u['city']}/{u['uf']}" if u["city"] else "", ("CEP " + fmt_cep(u["cep"])) if u["cep"] else ""] if x)
    contato = " · ".join(x for x in [cfg.get("whatsapp") or cfg.get("telefone"), cfg.get("email")] if x)
    lance = l["final_cents"] if l["winner_id"] == user_id and l["final_cents"] else (
        c.execute("SELECT MAX(amount_cents) FROM bids WHERE lot_id=? AND user_id=?", (lot_id, user_id)).fetchone()[0] or l["start_cents"])
    desc = l["title"].strip().rstrip(".") + "." + ((" " + re.sub(r"\s+", " ", l["description"]).strip()) if (l["description"] or "").strip() else "")
    return {
        "leilao_num": f"{l['aid']:04d}" if l["aid"] else "Lote avulso",
        "leilao_data": datetime.fromtimestamp(when, TZ).strftime("%d/%m/%Y, às %Hh%M"),
        "modalidade": MOD_NOTA.get(l["modality"] or "Online", "Leilão online (eletrônico)"),
        "lote_num": lotno(l), "comitente": "", "descricao": desc[:1200],
        "arr_nome": u["name"], "arr_cpf": fmt_cpf(u["cpf"]) if u["cpf"] else "", "arr_rg": u["rg"] or "",
        "arr_endereco": "\n".join(x for x in [end, end2] if x), "arr_whatsapp": fmt_phone(u["phone"]) if u["phone"] else "", "arr_email": u["email"],
        "lance": lance or 0, "comissao_pct": l["commission_pct"] if l["commission_pct"] is not None else pct_of(cfg.get("comissao_padrao"), 5.0),
        "taxa_adm": parse_money(cfg.get("taxa_adm_padrao")) or 0, "patio_diarias": 0, "patio_valor": parse_money(cfg.get("taxa_patio_dia")) or 0,
        "outras": [{"desc": "", "valor": 0} for _ in range(3)],
        "emp_razao": razao, "emp_cnpj": cfg.get("cnpj", ""), "emp_leiloeiro": cfg.get("leiloeiro_nome", ""), "emp_jucesp": cfg.get("leiloeiro_matricula", ""),
        "emp_contato": contato, "emp_site": site_host(), "emp_cidade": cfg.get("cidade", ""),
        "pix": cfg.get("pix_chave", ""), "favorecido": cfg.get("favorecido") or razao, "banco": cfg.get("banco", ""), "agencia_conta": cfg.get("agencia_conta", ""),
        "prazo_pagamento": cfg.get("prazo_pagamento") or "48 horas úteis após o leilão", "multa_pct": pct_of(cfg.get("multa_pct"), 20.0),
        "retirada_local": cfg.get("retirada_local") or l["location"] or "", "retirada_horarios": cfg.get("retirada_horarios") or cfg.get("horario", ""),
        "prazo_retirada": cfg.get("prazo_retirada") or "Até 5 dias úteis após a confirmação do pagamento",
        "levar": NOTA_LEVAR, "documentacao": NOTA_DOCS, "condicoes": NOTA_COND, "observacoes": "",
    }

def create_nota(c, lot_id, user_id, regenerate=False):
    """Cria (ou regenera) a nota do par lote+cliente. Retorna o id. Numeração sequencial por ano."""
    d = nota_defaults(c, lot_id, user_id)
    ex = c.execute("SELECT id FROM notas WHERE lot_id=? AND user_id=?", (lot_id, user_id)).fetchone()
    if ex:
        if regenerate: c.execute("UPDATE notas SET data=?, updated_at=? WHERE id=?", (json.dumps(d, ensure_ascii=False), now(), ex["id"]))
        return ex["id"]
    l = c.execute("SELECT closed_at FROM lots WHERE id=?", (lot_id,)).fetchone()
    year = datetime.fromtimestamp((l["closed_at"] if l and l["closed_at"] else now()), TZ).year
    for _ in range(5):
        seq = (c.execute("SELECT MAX(seq) FROM notas WHERE year=?", (year,)).fetchone()[0] or 0) + 1
        try:
            return c.execute("INSERT INTO notas(lot_id,user_id,year,seq,status,data,issued_at,updated_at) VALUES(?,?,?,?,'Pendente',?,?,?)",
                             (lot_id, user_id, year, seq, json.dumps(d, ensure_ascii=False), now(), now())).lastrowid
        except sqlite3.IntegrityError:
            ex = c.execute("SELECT id FROM notas WHERE lot_id=? AND user_id=?", (lot_id, user_id)).fetchone()
            if ex: return ex["id"]
    raise RuntimeError("não foi possível numerar a nota")

def nota_num(n): return f"{n['seq']:04d}/{n['year']}"

def nota_calc(d):
    lance = int(d.get("lance") or 0); pct = float(d.get("comissao_pct") or 0)
    com = round(lance * pct / 100); adm = int(d.get("taxa_adm") or 0)
    pat = int(d.get("patio_diarias") or 0) * int(d.get("patio_valor") or 0)
    outras = [o for o in d.get("outras", []) if (o.get("desc") or o.get("valor"))]
    tot = lance + com + adm + pat + sum(int(o.get("valor") or 0) for o in outras)
    return dict(lance=lance, pct=pct, com=com, adm=adm, pat=pat, outras=outras, total=tot)

def pix_key_norm(k):
    k = (k or "").strip()
    if "@" in k: return k.lower()
    if re.fullmatch(r"[\d.\-/ ]+", k) and len(only_digits(k)) in (11, 14) and not k.startswith("+"): return only_digits(k)
    if k.startswith("+"): return "+" + only_digits(k)
    return k

def pix_payload(key, name, city, cents, txid="***"):
    """BR Code PIX estático (copia e cola) com valor."""
    import unicodedata
    def clean(s, n): return re.sub(r"[^A-Za-z0-9 ]", "", unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()).upper()[:n] or "NA"
    f = lambda i, v: f"{i}{len(v):02d}{v}"
    mai = f("00", "br.gov.bcb.pix") + f("01", key)
    p = f("00", "01") + f("26", mai) + f("52", "0000") + f("53", "986") + (f("54", f"{cents/100:.2f}") if cents else "") + \
        f("58", "BR") + f("59", clean(name, 25)) + f("60", clean(city, 15)) + f("62", f("05", txid)) + "6304"
    crc = 0xFFFF
    for ch in p.encode():
        crc ^= ch << 8
        for _ in range(8): crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
        crc &= 0xFFFF
    return p + f"{crc:04X}"

def nota_ctx(n):
    d = json.loads(n["data"]); t = nota_calc(d); pix = qr = ""
    if d.get("pix") and n["status"] != "Cancelada":
        cidade = (d.get("emp_cidade") or "Sao Paulo").split("/")[0]
        pix = pix_payload(pix_key_norm(d["pix"]), d.get("favorecido") or d.get("emp_razao"), cidade, t["total"], f"NOTA{n['seq']:04d}{n['year']}")
        qr = segno.make(pix, error="m").png_data_uri(scale=6, border=1)
    lines = lambda k: [x.strip() for x in (d.get(k) or "").splitlines() if x.strip()]
    return dict(n=n, d=d, t=t, num=nota_num(n), pix=pix, qr=qr, lines=lines, issued=datetime.fromtimestamp(n["issued_at"], TZ).strftime("%d/%m/%Y"))

def nota_pdf_bytes(n):
    import weasyprint
    html = render_template("nota_doc.html", pdf=True, **nota_ctx(n))
    return weasyprint.HTML(string=html, base_url=BASE).write_pdf()

def nota_for(nid):
    """Nota visível para o usuário atual (admin ou o próprio arrematante); senão 404."""
    me = current_user(); n = db().execute("SELECT * FROM notas WHERE id=?", (nid,)).fetchone()
    if not n or not me or not (me["is_admin"] or n["user_id"] == me["id"]): abort(404)
    return n

@app.get("/nota/<int:nid>")
@login_required
def nota_view(nid):
    n = nota_for(nid)
    resp = app.make_response(render_template("nota_doc.html", pdf=False, embed=bool(request.args.get("embed")), **nota_ctx(n)))
    if request.args.get("embed") and current_user()["is_admin"]:
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Content-Security-Policy"] = "frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'"
    return resp

@app.get("/nota/<int:nid>.pdf")
@login_required
def nota_pdf(nid):
    n = nota_for(nid)
    try: pdf = nota_pdf_bytes(n)
    except Exception as e:
        print("nota pdf erro:", nid, type(e).__name__, str(e)[:200], flush=True)
        return render_template("msg.html", title="PDF indisponível", text="Não foi possível gerar o PDF agora. Tente novamente em instantes."), 500
    resp = app.make_response(pdf); resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = f'{"attachment" if request.args.get("baixar") else "inline"}; filename="nota-arrematacao-{n["seq"]:04d}-{n["year"]}.pdf"'
    return resp

_PDF_HEALTH = {}
@app.get("/health/pdf")
def health_pdf():
    """Teste público e seguro do gerador de PDF (sem dados): renderiza um documento mínimo."""
    if _PDF_HEALTH.get("ts", 0) > now() - 300: return _PDF_HEALTH["r"]
    t0 = time.time()
    try:
        import weasyprint
        b = weasyprint.HTML(string="<html><body style='font-family:Inter'><h1>SP Leilões</h1><div style='display:grid;grid-template-columns:1fr 1fr'><p>a</p><p>b</p></div></body></html>").write_pdf()
        r = {"ok": b[:5] == b"%PDF-", "renderer": "weasyprint " + weasyprint.__version__, "bytes": len(b), "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        r = {"ok": False, "error": type(e).__name__}
    _PDF_HEALTH.update(ts=now(), r=r); return r

NOTAQ = """SELECT n.*, l.title lot_title, l.lot_number, l.auction_id, u.name u_name, u.email u_email FROM notas n
  JOIN lots l ON l.id=n.lot_id JOIN users u ON u.id=n.user_id"""

@app.get("/admin/notas")
@admin_required
def admin_notas():
    c = db(); a = request.args; w, args = ["1=1"], []
    if a.get("status") in NOTA_STATUS: w.append("n.status=?"); args.append(a["status"])
    if (a.get("cliente") or "").isdigit(): w.append("n.user_id=?"); args.append(int(a["cliente"]))
    if (a.get("lote") or "").isdigit(): w.append("n.lot_id=?"); args.append(int(a["lote"]))
    q = (a.get("q") or "").strip()[:80]
    if q: w.append("(u.name LIKE ? OR u.email LIKE ? OR l.title LIKE ? OR printf('%04d/%d', n.seq, n.year) LIKE ?)"); args += [f"%{q}%"] * 4
    rows = c.execute(NOTAQ + " WHERE " + " AND ".join(w) + " ORDER BY n.year DESC, n.seq DESC LIMIT 300", args).fetchall()
    notas = [(r, nota_calc(json.loads(r["data"]))) for r in rows]
    clients = c.execute("SELECT DISTINCT u.id, u.name, u.email FROM users u JOIN notas n ON n.user_id=u.id ORDER BY u.name").fetchall()
    lots = c.execute("SELECT DISTINCT l.id, l.title, l.lot_number FROM lots l JOIN notas n ON n.lot_id=l.id ORDER BY l.id DESC").fetchall()
    closed = c.execute("SELECT l.id, l.title, l.lot_number, l.winner_id, u.name w_name FROM lots l LEFT JOIN users u ON u.id=l.winner_id WHERE l.status='closed' ORDER BY l.closed_at DESC LIMIT 300").fetchall()
    users = c.execute("SELECT id, name, email FROM users WHERE is_admin=0 ORDER BY name").fetchall()
    return render_template("admin_notas.html", notas=notas, clients=clients, lots=lots, closed=closed, users=users, f=a, NOTA_STATUS=NOTA_STATUS, num=nota_num)

@app.post("/admin/notas/gerar")
@admin_required
def admin_nota_gerar():
    c = db(); f = request.form
    lid = int(f["lote"]) if (f.get("lote") or "").isdigit() else None
    l = c.execute("SELECT * FROM lots WHERE id=?", (lid,)).fetchone() if lid else None
    uid = int(f["cliente"]) if (f.get("cliente") or "").isdigit() else (l["winner_id"] if l else None)
    if not l or not uid or not c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
        flash("Escolha um lote e um cliente (ou um lote arrematado, para usar o vencedor).", "err"); return redirect(url_for("admin_notas"))
    existed = c.execute("SELECT id FROM notas WHERE lot_id=? AND user_id=?", (lid, uid)).fetchone()
    nid = create_nota(c, lid, uid, regenerate=bool(f.get("regenerar")))
    flash("Nota regenerada com os dados atuais." if existed and f.get("regenerar") else ("Esta nota já existia — abrindo." if existed else "Nota gerada."), "ok")
    return redirect(url_for("admin_nota", nid=nid))

TXT_FIELDS = ["leilao_num", "leilao_data", "modalidade", "lote_num", "comitente", "arr_nome", "arr_cpf", "arr_rg", "arr_whatsapp", "arr_email",
              "emp_razao", "emp_cnpj", "emp_leiloeiro", "emp_jucesp", "emp_contato", "emp_site", "emp_cidade", "pix", "favorecido", "banco", "agencia_conta",
              "prazo_pagamento", "retirada_local", "retirada_horarios", "prazo_retirada"]
AREA_FIELDS = {"descricao": 1500, "arr_endereco": 300, "levar": 800, "documentacao": 2500, "condicoes": 2500, "observacoes": 1500}

@app.route("/admin/nota/<int:nid>", methods=["GET", "POST"])
@admin_required
def admin_nota(nid):
    c = db(); n = c.execute("SELECT * FROM notas WHERE id=?", (nid,)).fetchone()
    if not n: abort(404)
    if request.method == "POST":
        f = request.form; act = f.get("acao") or "salvar"
        if act == "salvar":
            d = json.loads(n["data"])
            for k in TXT_FIELDS: d[k] = (f.get(k) or "").strip()[:200]
            for k, mx in AREA_FIELDS.items(): d[k] = (f.get(k) or "").strip()[:mx]
            bad = [lbl for k, lbl in [("lance", "lance"), ("taxa_adm", "taxa administrativa"), ("patio_valor", "valor da diária")] if money0(f.get(k)) is None]
            if bad: flash("Valor inválido em: " + ", ".join(bad) + ".", "err"); return redirect(url_for("admin_nota", nid=nid))
            d["lance"] = money0(f.get("lance")); d["taxa_adm"] = money0(f.get("taxa_adm")); d["patio_valor"] = money0(f.get("patio_valor"))
            d["comissao_pct"] = pct_of(f.get("comissao_pct"), 5.0); d["multa_pct"] = pct_of(f.get("multa_pct"), 20.0)
            d["patio_diarias"] = max(0, min(999, int(f.get("patio_diarias")) if (f.get("patio_diarias") or "").isdigit() else 0))
            d["outras"] = [{"desc": (f.get(f"outra_desc_{i}") or "").strip()[:80], "valor": money0(f.get(f"outra_valor_{i}")) or 0} for i in range(3)]
            st = f.get("status") if f.get("status") in NOTA_STATUS else n["status"]
            c.execute("UPDATE notas SET data=?, status=?, updated_at=? WHERE id=?", (json.dumps(d, ensure_ascii=False), st, now(), nid))
            flash("Nota salva. Totais recalculados.", "ok")
        elif act == "regenerar":
            create_nota(c, n["lot_id"], n["user_id"], regenerate=True); flash("Nota regenerada com os dados atuais do lote, do cliente e da empresa.", "ok")
        elif act == "enviar":
            d = json.loads(n["data"]); to = (d.get("arr_email") or "").strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to): flash("E-mail do arrematante inválido na nota.", "err")
            elif not mail_mode(): flash("Envio de e-mail não configurado.", "err")
            else:
                try: pdf = nota_pdf_bytes(n)
                except Exception as e:
                    print("nota pdf erro:", nid, type(e).__name__, str(e)[:200], flush=True); flash("Não foi possível gerar o PDF.", "err"); return redirect(url_for("admin_nota", nid=nid))
                t = nota_calc(d); emp = get_cfg().get("empresa", "SP Leilões"); num = nota_num(n)
                text = (f"Olá, {short_name(d.get('arr_nome'))}!\n\nSegue em anexo a Nota de Arrematação nº {num} referente ao lote {d.get('lote_num')} "
                        f"({(d.get('descricao') or '')[:80]}).\nTotal a pagar: {brl(t['total'])}. Prazo de pagamento: {d.get('prazo_pagamento')}.\n\n"
                        f"Você também pode ver a nota em Minha conta no site.\n\n{emp}")
                html = (f"<div style='font-family:Arial,sans-serif;max-width:560px'><h2 style='color:#b4141c'>{Markup.escape(emp)}</h2>"
                        f"<p>Olá, {Markup.escape(short_name(d.get('arr_nome')))}!</p><p>Segue em anexo a <b>Nota de Arrematação nº {num}</b> referente ao lote "
                        f"<b>{Markup.escape(d.get('lote_num') or '')}</b>.</p><p>Total a pagar: <b>{brl(t['total'])}</b><br>Prazo de pagamento: {Markup.escape(d.get('prazo_pagamento') or '')}</p>"
                        f"<p style='color:#666;font-size:13px'>A nota também fica disponível em <b>Minha conta</b> no site.</p></div>")
                if send_mail(to, f"Nota de Arrematação nº {num} — {emp}", text, html, "nota", attachments=[(f"nota-arrematacao-{n['seq']:04d}-{n['year']}.pdf", pdf)]):
                    c.execute("UPDATE notas SET sent_at=?, sent_count=COALESCE(sent_count,0)+1 WHERE id=?", (now(), nid)); flash(f"Nota enviada para {to}.", "ok")
                else: flash("Falha ao enviar o e-mail (veja o log de e-mails em Clientes).", "err")
        return redirect(url_for("admin_nota", nid=nid))
    ctxd = nota_ctx(n)
    l = c.execute("SELECT id, title, winner_id, status FROM lots WHERE id=?", (n["lot_id"],)).fetchone()
    u = c.execute("SELECT id, name, email FROM users WHERE id=?", (n["user_id"],)).fetchone()
    return render_template("admin_nota.html", lot=l, cli=u, NOTA_STATUS=NOTA_STATUS, mode=mail_mode(), **ctxd)

def notas_of_user(uid):
    return db().execute(NOTAQ + " WHERE n.user_id=? ORDER BY n.year DESC, n.seq DESC", (uid,)).fetchall()
app.jinja_env.globals.update(nota_num=nota_num, nota_total=lambda r: nota_calc(json.loads(r["data"]))["total"])

@app.errorhandler(403)
def e403(_): return render_template("msg.html", title="Acesso negado", text="Área restrita ao administrador."), 403
@app.errorhandler(404)
def e404(_): return render_template("msg.html", title="Não encontrado", text="Essa página ou lote não existe."), 404
@app.errorhandler(413)
def e413(_): return render_template("msg.html", title="Arquivos muito grandes", text="Envie fotos menores (até 30 MB no total)."), 413

# ---------- inicialização ----------
def purge_examples(c):
    """Remove os leilões/lotes de demonstração das versões de teste (v0.1–v0.4)."""
    for l in c.execute("SELECT id FROM lots WHERE is_example=1").fetchall(): remove_lot(c, l["id"])
    for a in c.execute("SELECT id FROM auctions WHERE is_example=1").fetchall():
        c.execute("DELETE FROM habilitations WHERE auction_id=?", (a["id"],)); c.execute("UPDATE lots SET auction_id=NULL WHERE auction_id=?", (a["id"],))
        c.execute("DELETE FROM auctions WHERE id=?", (a["id"],))
    ex = c.execute("SELECT id FROM users WHERE email='exemplo@spleiloes.teste'").fetchone()
    if ex:
        for t in ("bids", "favorites", "habilitations"): c.execute(f"DELETE FROM {t} WHERE user_id=?", (ex["id"],))
        c.execute("DELETE FROM users WHERE id=?", (ex["id"],))
    for fn in os.listdir(UPLOADS):
        if fn.startswith("ex_"):
            try: os.remove(os.path.join(UPLOADS, fn))
            except OSError: pass

def init():
    c = connect(); c.executescript(SCHEMA)
    have = {r["name"] for r in c.execute("PRAGMA table_info(lots)")}
    for col, typ in LOT_COLS.items():
        if col not in have: c.execute(f"ALTER TABLE lots ADD COLUMN {col} {typ}")
    have = {r["name"] for r in c.execute("PRAGMA table_info(users)")}
    for col, typ in USER_COLS.items():
        if col not in have: c.execute(f"ALTER TABLE users ADD COLUMN {col} {typ}")
    have = {r["name"] for r in c.execute("PRAGMA table_info(auctions)")}
    for col, typ in AUC_COLS.items():
        if col not in have: c.execute(f"ALTER TABLE auctions ADD COLUMN {col} {typ}")
    if "fila" not in {r["name"] for r in c.execute("PRAGMA table_info(banners)")}:
        c.execute("ALTER TABLE banners ADD COLUMN fila INTEGER DEFAULT 1")
    c.execute("UPDATE auctions SET start_ts=created_at WHERE start_ts IS NULL")
    c.execute("UPDATE auctions SET edital=? WHERE edital IS NULL OR edital=''", (DEF_EDITAL,))
    c.execute("UPDATE lots SET fees=? WHERE fees IS NULL OR fees=''", (DEF_FEES,))
    if not c.execute("SELECT 1 FROM meta WHERE k='cats_v10'").fetchone():
        q = ",".join("?" * len(CATEGORIAS))
        c.execute(f"UPDATE lots SET category='Veículos' WHERE category IS NULL OR category NOT IN ({q})", CATEGORIAS)
        c.execute("INSERT OR IGNORE INTO meta VALUES('cats_v10', ?)", (str(now()),))
    c.execute("UPDATE lots SET conditions=? WHERE conditions IS NULL OR conditions='' OR conditions IN (?,?)", (DEF_COND, OLD_COND, V3_COND))
    c.execute("UPDATE lots SET pickup=? WHERE pickup IN (?,?)", (DEF_PICK, OLD_PICK, V3_PICK))
    c.execute("UPDATE lots SET fees=? WHERE fees=?", (DEF_FEES, V4_FEES))
    c.execute("UPDATE auctions SET edital=? WHERE edital=?", (DEF_EDITAL, V4_EDITAL))
    c.execute("UPDATE lots SET pickup=? WHERE pickup IS NULL OR pickup=''", (DEF_PICK,))
    email = (os.environ.get("ADMIN_EMAIL") or "admin@spleiloes.teste").lower()
    pw = os.environ.get("ADMIN_PASSWORD")
    if pw:
        h = generate_password_hash(pw)
        u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        adm = c.execute("SELECT * FROM users WHERE is_admin=1 ORDER BY id LIMIT 1").fetchone()
        if u:
            c.execute("UPDATE users SET pw_hash=?, is_admin=1 WHERE id=?", (h, u["id"]))
            if adm and adm["id"] != u["id"]:
                c.execute("UPDATE users SET is_admin=0 WHERE id=?", (adm["id"],))
                print(f"Admin transferido para {email} (#{u['id']}); #{adm['id']} ({adm['email']}) deixou de ser admin.", flush=True)
        elif adm:
            old_email = adm["email"]
            c.execute("UPDATE users SET email=?, pw_hash=? WHERE id=?", (email, h, adm["id"]))
            print(f"Admin e-mail migrado: {old_email} → {email}", flush=True)
        else:
            c.execute("INSERT INTO users(name,email,pw_hash,is_admin,created_at) VALUES('Pedro (Admin)',?,?,1,?)", (email, h, now()))
    c.execute("UPDATE users SET email_verified=1, email_verified_by=COALESCE(email_verified_by,'admin') WHERE is_admin=1 AND COALESCE(email_verified,0)=0")
    if os.environ.get("ADMIN_2FA_RESET") == "1":
        c.execute("UPDATE users SET totp_secret=NULL, totp_enabled=0, recovery_hashes='[]' WHERE email=?", (email,))
        c.execute("INSERT INTO login_log(ts,ip,email,kind) VALUES(?,?,?,?)", (now(), "boot", email, "2fa_reset_env"))
        print("ADMIN_2FA_RESET=1: confirmação em dois passos do admin DESATIVADA. Remova a variável.", flush=True)
    c.execute("DELETE FROM login_log WHERE ts<?", (now() - 90 * 86400,))
    for l in c.execute("SELECT id, winner_id FROM lots l WHERE status='closed' AND winner_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM notas n WHERE n.lot_id=l.id AND n.user_id=l.winner_id)").fetchall():
        try: create_nota(c, l["id"], l["winner_id"]); print("nota gerada para lote existente", l["id"], flush=True)
        except Exception as e: print("nota init:", l["id"], e, flush=True)
    if not c.execute("SELECT 1 FROM meta WHERE k='purge_v5'").fetchone():
        c.execute("BEGIN IMMEDIATE")
        if not c.execute("SELECT 1 FROM meta WHERE k='purge_v5'").fetchone():
            purge_examples(c); c.execute("INSERT INTO meta VALUES('purge_v5', ?)", (str(now()),))
        c.execute("COMMIT")
    c.close()

init()
threading.Thread(target=closer_loop, daemon=True).start()
threading.Thread(target=backup_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
