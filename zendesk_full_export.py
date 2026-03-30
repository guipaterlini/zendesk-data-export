"""
Zendesk Full Data Exporter — Large Scale
==========================================
Script otimizado para exportar TODOS os dados de uma conta Zendesk grande.

Features:
  - Incremental Export (cursor-based) para tickets e usuários
  - Paginação automática com cursor para todos os endpoints
  - Rate limiting inteligente (respeita Retry-After)
  - Resume/checkpoint: se cair no meio, retoma de onde parou
  - Salva em NDJSON (1 registro por linha) para não estourar memória
  - Exporta comentários de tickets em lotes
  - Log detalhado de progresso
  - Relatório final com contagens

Uso:
    1. Copie o arquivo .env.example para .env e preencha suas credenciais
    2. python3 zendesk_full_export.py

Requisitos:
    pip3 install requests python-dotenv
"""

import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

# Carrega variáveis do arquivo .env (se existir)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Se não tiver python-dotenv, usa variáveis de ambiente do sistema

try:
    import requests
except ImportError:
    print("❌ Biblioteca 'requests' não encontrada.")
    print("   Instale com: pip3 install requests")
    sys.exit(1)


# ============================================================================
# CONFIGURAÇÃO (via variáveis de ambiente)
# ============================================================================
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")
ZENDESK_EMAIL     = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")

# Diretório onde os arquivos serão salvos
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "zendesk_full_export")

# Exportar comentários de cada ticket? (lento — 1 request/ticket)
EXPORT_COMMENTS = os.getenv("EXPORT_COMMENTS", "true").lower() == "true"

# Limite de comentários por batch para checkpoint (0 = sem limite)
COMMENTS_BATCH_SIZE = int(os.getenv("COMMENTS_BATCH_SIZE", "500"))

# Início para incremental exports (unix timestamp, 0 = tudo)
START_TIME = int(os.getenv("START_TIME", "0"))

# Quantas retries em caso de erro de rede
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

# ============================================================================
# NÃO EDITAR ABAIXO (a menos que saiba o que está fazendo)
# ============================================================================

BASE_URL = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com"
AUTH = (f"{ZENDESK_EMAIL}/token", ZENDESK_API_TOKEN)
SESSION = requests.Session()
SESSION.auth = AUTH
SESSION.headers.update({"Content-Type": "application/json", "Accept": "application/json"})

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(OUTPUT_DIR, "export.log"), mode="a", encoding="utf-8"),
    ] if os.path.isdir(OUTPUT_DIR) else [logging.StreamHandler()],
)
log = logging.getLogger("zendesk_export")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_dirs():
    """Cria estrutura de diretórios."""
    for sub in ["tickets", "users", "organizations", "comments",
                 "groups", "brands", "ticket_fields", "user_fields",
                 "organization_fields", "ticket_forms", "macros",
                 "views", "triggers", "automations", "sla_policies",
                 "schedules", "tags", "custom_roles"]:
        Path(OUTPUT_DIR, sub).mkdir(parents=True, exist_ok=True)


def api_get(url, params=None):
    """
    GET com retry exponencial e rate-limit handling.
    Retorna (json_data, response_headers) ou (None, None).
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(url, params=params, timeout=60)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 90))
                log.warning(f"Rate limit (429). Aguardando {wait}s...")
                time.sleep(wait)
                continue

            if resp.status_code == 404:
                log.warning(f"404 Not Found: {url}")
                return None, None

            resp.raise_for_status()
            time.sleep(0.25)  # throttle gentil
            return resp.json(), resp.headers

        except requests.exceptions.RequestException as e:
            wait = min(2 ** attempt, 60)
            log.warning(f"Erro (tentativa {attempt}/{MAX_RETRIES}): {e}. Retry em {wait}s...")
            time.sleep(wait)

    log.error(f"Falha após {MAX_RETRIES} tentativas: {url}")
    return None, None


class NDJSONWriter:
    """Escreve registros em formato NDJSON (1 JSON por linha), com flush periódico."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.file = open(filepath, "a", encoding="utf-8")
        self.count = 0

    def write(self, record):
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1
        if self.count % 500 == 0:
            self.file.flush()

    def close(self):
        self.file.flush()
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def load_checkpoint(name):
    """Carrega checkpoint de progresso."""
    path = os.path.join(OUTPUT_DIR, f".checkpoint_{name}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return None


def save_checkpoint(name, data):
    """Salva checkpoint."""
    path = os.path.join(OUTPUT_DIR, f".checkpoint_{name}.json")
    with open(path, "w") as f:
        json.dump(data, f)


def clear_checkpoint(name):
    """Remove checkpoint após sucesso."""
    path = os.path.join(OUTPUT_DIR, f".checkpoint_{name}.json")
    if os.path.exists(path):
        os.remove(path)


def count_lines(filepath):
    """Conta linhas de um arquivo NDJSON."""
    if not os.path.exists(filepath):
        return 0
    with open(filepath, "r", encoding="utf-8") as f:
        return sum(1 for _ in f)


# ---------------------------------------------------------------------------
# Incremental Exports (tickets e usuários — cursor-based)
# ---------------------------------------------------------------------------

def export_incremental(resource, key):
    """
    Exporta via Incremental Export API (cursor-based).
    Funciona para: tickets, users.
    """
    label = resource.capitalize()
    filepath = os.path.join(OUTPUT_DIR, resource, f"{resource}.ndjson")

    # Resume
    checkpoint = load_checkpoint(f"incremental_{resource}")
    cursor = None
    existing_count = 0
    if checkpoint:
        cursor = checkpoint.get("cursor")
        existing_count = count_lines(filepath)
        log.info(f"▶ Retomando {label} do checkpoint ({existing_count} já exportados)")

    total = existing_count
    start_time = max(START_TIME, 1)

    with NDJSONWriter(filepath) as writer:
        writer.count = existing_count  # ajusta contagem p/ não sobrescrever

        if cursor:
            url = f"{BASE_URL}/api/v2/incremental/{resource}/cursor.json"
            params = {"cursor": cursor}
        else:
            url = f"{BASE_URL}/api/v2/incremental/{resource}/cursor.json"
            params = {"start_time": start_time}

        page = 0
        while True:
            page += 1
            data, _ = api_get(url, params)
            if not data:
                log.error(f"Erro ao exportar {label}, página {page}. Checkpoint salvo.")
                return total

            records = data.get(key, [])
            for rec in records:
                writer.write(rec)
            total += len(records)

            if page % 5 == 0 or len(records) > 0:
                log.info(f"  {label}: página {page} — {total} registros")

            # Checkpoint a cada página
            after_cursor = data.get("after_cursor")
            save_checkpoint(f"incremental_{resource}", {"cursor": after_cursor})

            if data.get("end_of_stream", False):
                break
            if not after_cursor:
                break

            url = f"{BASE_URL}/api/v2/incremental/{resource}/cursor.json"
            params = {"cursor": after_cursor}

    clear_checkpoint(f"incremental_{resource}")
    log.info(f"✅ {label}: {total} registros exportados")
    return total


# ---------------------------------------------------------------------------
# Paginação padrão (cursor-based) para outros recursos
# ---------------------------------------------------------------------------

def export_paginated(endpoint, key, subfolder, filename=None):
    """
    Exporta qualquer endpoint com cursor pagination.
    endpoint: ex "/api/v2/organizations.json"
    key: chave do array na resposta, ex "organizations"
    """
    label = key.replace("_", " ").capitalize()
    fname = filename or f"{key}.ndjson"
    filepath = os.path.join(OUTPUT_DIR, subfolder, fname)

    # Resume
    checkpoint = load_checkpoint(f"paginated_{key}")
    next_url = None
    existing_count = 0
    if checkpoint:
        next_url = checkpoint.get("next_url")
        existing_count = count_lines(filepath)
        log.info(f"▶ Retomando {label} do checkpoint ({existing_count} já exportados)")

    total = existing_count
    page = 0

    with NDJSONWriter(filepath) as writer:
        writer.count = existing_count

        url = next_url or f"{BASE_URL}{endpoint}"
        params = {"page[size]": 100} if not next_url else None

        while True:
            page += 1
            data, _ = api_get(url, params)
            if not data:
                log.error(f"Erro ao exportar {label}, página {page}.")
                return total

            records = data.get(key, [])
            for rec in records:
                writer.write(rec)
            total += len(records)

            if page % 5 == 0:
                log.info(f"  {label}: página {page} — {total} registros")

            # Próxima página
            meta = data.get("meta", {})
            links = data.get("links", {})
            next_page = links.get("next") if meta.get("has_more") else None

            if not next_page:
                # Fallback: offset pagination
                next_page = data.get("next_page")

            if next_page:
                save_checkpoint(f"paginated_{key}", {"next_url": next_page})
                url = next_page
                params = None
            else:
                break

    clear_checkpoint(f"paginated_{key}")
    log.info(f"✅ {label}: {total} registros exportados")
    return total


# ---------------------------------------------------------------------------
# Exportação de comentários de tickets
# ---------------------------------------------------------------------------

def export_comments():
    """Exporta comentários de todos os tickets, com checkpoint por ticket."""
    tickets_file = os.path.join(OUTPUT_DIR, "tickets", "tickets.ndjson")
    if not os.path.exists(tickets_file):
        log.warning("Arquivo de tickets não encontrado. Exporte tickets primeiro.")
        return 0

    comments_file = os.path.join(OUTPUT_DIR, "comments", "comments.ndjson")

    # Coleta todos os IDs de ticket
    ticket_ids = []
    with open(tickets_file, "r", encoding="utf-8") as f:
        for line in f:
            try:
                t = json.loads(line)
                ticket_ids.append(t["id"])
            except (json.JSONDecodeError, KeyError):
                continue

    log.info(f"💬 Exportando comentários de {len(ticket_ids)} tickets...")

    # Checkpoint: último ticket processado
    checkpoint = load_checkpoint("comments")
    last_processed_id = checkpoint.get("last_ticket_id", 0) if checkpoint else 0

    # Filtra tickets já processados
    if last_processed_id:
        try:
            idx = ticket_ids.index(last_processed_id)
            ticket_ids = ticket_ids[idx + 1:]
            log.info(f"▶ Retomando comentários a partir do ticket {last_processed_id} ({len(ticket_ids)} restantes)")
        except ValueError:
            pass

    total = count_lines(comments_file)
    batch_count = 0

    with NDJSONWriter(comments_file) as writer:
        writer.count = total

        for i, tid in enumerate(ticket_ids, 1):
            url = f"{BASE_URL}/api/v2/tickets/{tid}/comments.json"
            next_url = url

            while next_url:
                data, _ = api_get(next_url)
                if not data:
                    break

                for comment in data.get("comments", []):
                    comment["ticket_id"] = tid
                    writer.write(comment)
                    total += 1

                next_url = data.get("next_page")

            batch_count += 1
            if batch_count % 100 == 0:
                log.info(f"  Comentários: {i}/{len(ticket_ids)} tickets processados — {total} comentários")
                save_checkpoint("comments", {"last_ticket_id": tid})

            if COMMENTS_BATCH_SIZE and batch_count >= COMMENTS_BATCH_SIZE:
                save_checkpoint("comments", {"last_ticket_id": tid})
                log.info(f"  Batch de {COMMENTS_BATCH_SIZE} tickets concluído. Reexecute para continuar.")
                return total

    clear_checkpoint("comments")
    log.info(f"✅ Comentários: {total} exportados")
    return total


# ---------------------------------------------------------------------------
# Exportação de endpoints simples (sem paginação ou paginação pequena)
# ---------------------------------------------------------------------------

def export_simple(endpoint, key, subfolder, filename=None):
    """Exporta endpoint simples (1 página ou poucas)."""
    fname = filename or f"{key}.json"
    filepath = os.path.join(OUTPUT_DIR, subfolder, fname)

    if os.path.exists(filepath) and os.path.getsize(filepath) > 10:
        log.info(f"⏭ {key} já exportado, pulando...")
        return

    data, _ = api_get(f"{BASE_URL}{endpoint}")
    if data and key in data:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data[key], f, ensure_ascii=False, indent=2)
        log.info(f"✅ {key}: {len(data[key])} registros")
    elif data:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info(f"✅ {key}: salvo")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def validate_config():
    errors = []
    if not ZENDESK_SUBDOMAIN:
        errors.append("ZENDESK_SUBDOMAIN")
    if not ZENDESK_EMAIL:
        errors.append("ZENDESK_EMAIL")
    if not ZENDESK_API_TOKEN:
        errors.append("ZENDESK_API_TOKEN")

    if errors:
        print("\n" + "=" * 60)
        print("  ❌ CREDENCIAIS NÃO CONFIGURADAS!")
        print("=" * 60)
        print(f"\n  Variáveis faltando: {', '.join(errors)}")
        print("\n  Opção 1 — Criar arquivo .env:")
        print("    Copie .env.example para .env e preencha suas credenciais")
        print("\n  Opção 2 — Exportar no terminal:")
        print('    export ZENDESK_SUBDOMAIN="sua_empresa"')
        print('    export ZENDESK_EMAIL="admin@empresa.com"')
        print('    export ZENDESK_API_TOKEN="seu_token"')
        print("\n  Para gerar o API Token:")
        print("    Central Admin > Apps e integrações > APIs > API do Zendesk")
        print("=" * 60)
        sys.exit(1)


def test_connection():
    log.info("🔌 Testando conexão...")
    data, _ = api_get(f"{BASE_URL}/api/v2/users/me.json")
    if data:
        u = data.get("user", {})
        log.info(f"✅ Conectado: {u.get('name')} ({u.get('email')}) — role: {u.get('role')}")
        return True
    log.error("❌ Falha na conexão. Verifique credenciais e subdomínio.")
    return False


def main():
    start = time.time()

    print("""
╔══════════════════════════════════════════════════════════╗
║           ZENDESK FULL DATA EXPORTER                     ║
║           Exportação completa para JSON                  ║
╚══════════════════════════════════════════════════════════╝
    """)

    validate_config()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Reconfigura logging com diretório existente
    file_handler = logging.FileHandler(
        os.path.join(OUTPUT_DIR, "export.log"), mode="a", encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(file_handler)

    if not test_connection():
        sys.exit(1)

    ensure_dirs()
    stats = {}

    # =======================================================================
    # 1. DADOS PRINCIPAIS (incremental — otimizado para grande volume)
    # =======================================================================
    log.info("\n" + "=" * 50)
    log.info("FASE 1: Dados principais (tickets, usuários, organizações)")
    log.info("=" * 50)

    stats["tickets"] = export_incremental("tickets", "tickets")
    stats["users"] = export_incremental("users", "users")
    stats["organizations"] = export_paginated(
        "/api/v2/organizations.json", "organizations", "organizations"
    )

    # =======================================================================
    # 2. COMENTÁRIOS DE TICKETS
    # =======================================================================
    if EXPORT_COMMENTS:
        log.info("\n" + "=" * 50)
        log.info("FASE 2: Comentários de tickets")
        log.info("=" * 50)
        stats["comments"] = export_comments()

    # =======================================================================
    # 3. CONFIGURAÇÕES E METADADOS
    # =======================================================================
    log.info("\n" + "=" * 50)
    log.info("FASE 3: Configurações e metadados")
    log.info("=" * 50)

    stats["groups"] = export_paginated(
        "/api/v2/groups.json", "groups", "groups"
    )
    export_simple("/api/v2/brands.json", "brands", "brands")
    export_simple("/api/v2/ticket_fields.json", "ticket_fields", "ticket_fields")
    export_simple("/api/v2/user_fields.json", "user_fields", "user_fields")
    export_simple("/api/v2/organization_fields.json", "organization_fields", "organization_fields")
    export_simple("/api/v2/ticket_forms.json", "ticket_forms", "ticket_forms")
    stats["tags"] = export_paginated(
        "/api/v2/tags.json", "tags", "tags"
    )

    # =======================================================================
    # 4. REGRAS DE NEGÓCIO
    # =======================================================================
    log.info("\n" + "=" * 50)
    log.info("FASE 4: Regras de negócio")
    log.info("=" * 50)

    stats["macros"] = export_paginated(
        "/api/v2/macros.json", "macros", "macros"
    )
    stats["views"] = export_paginated(
        "/api/v2/views.json", "views", "views"
    )
    stats["triggers"] = export_paginated(
        "/api/v2/triggers.json", "triggers", "triggers"
    )
    stats["automations"] = export_paginated(
        "/api/v2/automations.json", "automations", "automations"
    )
    export_simple("/api/v2/slas/policies.json", "sla_policies", "sla_policies")
    export_simple("/api/v2/business_hours/schedules.json", "schedules", "schedules")
    export_simple("/api/v2/custom_roles.json", "custom_roles", "custom_roles")

    # =======================================================================
    # RELATÓRIO FINAL
    # =======================================================================
    elapsed = time.time() - start
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    log.info("\n" + "=" * 60)
    log.info("  EXPORTAÇÃO CONCLUÍDA!")
    log.info(f"  Tempo total: {mins}m {secs}s")
    log.info("=" * 60)

    log.info("\n📊 Resumo:")
    for key, count in stats.items():
        log.info(f"   {key:.<30} {count:>8}")

    # Tamanho total
    total_size = 0
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            total_size += os.path.getsize(os.path.join(root, f))

    if total_size > 1024 ** 3:
        size_str = f"{total_size / 1024**3:.2f} GB"
    elif total_size > 1024 ** 2:
        size_str = f"{total_size / 1024**2:.1f} MB"
    else:
        size_str = f"{total_size / 1024:.0f} KB"

    log.info(f"\n   Tamanho total: {size_str}")
    log.info(f"   Local: {os.path.abspath(OUTPUT_DIR)}/")

    # Estrutura
    log.info("\n📁 Estrutura:")
    for root, dirs, files in sorted(os.walk(OUTPUT_DIR)):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        level = root.replace(OUTPUT_DIR, "").count(os.sep)
        indent = "   " + "│  " * level
        basename = os.path.basename(root)
        if level == 0:
            basename = OUTPUT_DIR
        log.info(f"{indent}📂 {basename}/")
        for f in sorted(files):
            if f.startswith("."):
                continue
            fpath = os.path.join(root, f)
            fsize = os.path.getsize(fpath)
            if fsize > 1024 * 1024:
                fs = f"{fsize / 1024**2:.1f}MB"
            elif fsize > 1024:
                fs = f"{fsize / 1024:.0f}KB"
            else:
                fs = f"{fsize}B"
            log.info(f"{indent}│  📄 {f} ({fs})")

    # Verifica se há checkpoints pendentes (exportação incompleta)
    pending = [f for f in os.listdir(OUTPUT_DIR) if f.startswith(".checkpoint_")]
    if pending:
        log.warning("\n⚠️  Existem checkpoints pendentes (exportação parcial):")
        for p in pending:
            log.warning(f"    {p}")
        log.warning("   Execute novamente para retomar de onde parou.")


if __name__ == "__main__":
    main()
