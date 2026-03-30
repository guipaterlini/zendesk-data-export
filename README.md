# 🚀 Zendesk Full Data Exporter

Script Python para exportar **todos os dados** de uma conta Zendesk via API REST — tickets, usuários, organizações, comentários, configurações e regras de negócio.

Testado com contas de **400k+ tickets** e **300k+ usuários** (9 GB+ de dados exportados).

---

## ✨ Features

| Feature                          | Descrição                                                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------------ |
| **Exportação completa**          | Tickets, usuários, organizações, comentários, grupos, tags, views, triggers, macros e mais |
| **Otimizado para grande volume** | Usa Incremental Export API (cursor-based) — funciona com milhões de registros              |
| **Não estoura memória**          | Salva em NDJSON (1 registro por linha), grava direto no disco                              |
| **Resume automático**            | Se cair no meio, basta rodar de novo — retoma de onde parou                                |
| **Rate limiting inteligente**    | Respeita os limites da API do Zendesk automaticamente                                      |
| **Comentários em batches**       | Exporta comentários de tickets em lotes configuráveis                                      |
| **Log detalhado**                | Progresso em tempo real no terminal + arquivo de log                                       |

---

## 📋 Pré-requisitos

- **Python 3.8+** instalado no computador
- **Conta Zendesk** com acesso de administrador ou agente
- **API Token** do Zendesk

### Como verificar se o Python está instalado

Abra o terminal (Mac/Linux) ou Prompt de Comando (Windows) e digite:

```bash
python3 --version
```

Se aparecer algo como `Python 3.12.0`, está tudo certo. Se der erro, baixe o Python em [python.org](https://www.python.org/downloads/).

### Como gerar o API Token no Zendesk

1. Acesse sua conta Zendesk
2. Vá em **Central de Administração** (ícone de engrenagem)
3. Navegue até **Apps e integrações → APIs → API do Zendesk**
4. Clique em **Adicionar API Token**
5. Dê um nome (ex: "Exportação de dados") e clique em **Copiar**
6. **Salve o token** — ele não será mostrado novamente

---

## 🛠️ Instalação

### 1. Baixe o projeto

```bash
git clone https://github.com/SEU_USUARIO/zendesk-full-exporter.git
cd zendesk-full-exporter
```

Ou baixe o ZIP clicando no botão verde **Code → Download ZIP** e descompacte.

### 2. Instale as dependências

```bash
pip3 install -r requirements.txt
```

### 3. Configure suas credenciais

Copie o arquivo de exemplo e preencha com seus dados:

```bash
cp .env.example .env
```

Abra o arquivo `.env` com qualquer editor de texto e preencha:

```
ZENDESK_SUBDOMAIN="minhaempresa"
ZENDESK_EMAIL="admin@minhaempresa.com"
ZENDESK_API_TOKEN="abc123tokenAqui"
```

> **O subdomínio** é a parte antes de `.zendesk.com` na URL da sua conta.
> Por exemplo, se sua URL é `https://minhaempresa.zendesk.com`, o subdomínio é `minhaempresa`.

> ⚠️ **Nunca compartilhe o arquivo `.env`** — ele contém suas credenciais de acesso.

---

## ▶️ Como usar

### Executar a exportação

```bash
python3 zendesk_full_export.py
```

O script vai:

1. Testar a conexão com sua conta
2. Exportar tickets (Incremental API)
3. Exportar usuários (Incremental API)
4. Exportar organizações
5. Exportar comentários de tickets (em batches)
6. Exportar configurações (grupos, campos, formulários, etc.)
7. Exportar regras de negócio (views, triggers, macros, etc.)
8. Mostrar um resumo com contagens e tamanho total

### Acompanhar o progresso

O script mostra o progresso em tempo real no terminal:

```
✅ Conectado: João Silva (joao@empresa.com) — role: admin

FASE 1: Dados principais
  Tickets: página 50 — 50000 registros
  Tickets: página 100 — 100000 registros
  ...
✅ Tickets: 411370 registros exportados
✅ Users: 302802 registros exportados
```

### Se a exportação parar no meio

Basta rodar o mesmo comando novamente:

```bash
python3 zendesk_full_export.py
```

O script detecta os checkpoints e **retoma de onde parou** — não precisa começar do zero.

---

## 📁 Estrutura dos dados exportados

```
zendesk_full_export/
├── tickets/
│   └── tickets.ndjson          # Todos os tickets
├── users/
│   └── users.ndjson            # Todos os usuários
├── organizations/
│   └── organizations.ndjson    # Todas as organizações
├── comments/
│   └── comments.ndjson         # Comentários dos tickets
├── groups/
│   └── groups.ndjson           # Grupos de agentes
├── brands/
│   └── brands.json             # Marcas/brands
├── ticket_fields/
│   └── ticket_fields.json      # Campos customizados de ticket
├── user_fields/
│   └── user_fields.json        # Campos customizados de usuário
├── organization_fields/
│   └── organization_fields.json
├── ticket_forms/
│   └── ticket_forms.json       # Formulários de ticket
├── tags/
│   └── tags.ndjson             # Tags
├── macros/
│   └── macros.ndjson           # Macros
├── views/
│   └── views.ndjson            # Views
├── triggers/
│   └── triggers.ndjson         # Triggers
├── automations/
│   └── automations.ndjson      # Automações
├── sla_policies/
│   └── sla_policies.json       # Políticas de SLA
├── schedules/
│   └── schedules.json          # Horários de operação
├── custom_roles/
│   └── custom_roles.json       # Roles customizados
└── export.log                  # Log completo da exportação
```

### Sobre o formato NDJSON

Arquivos `.ndjson` contêm **1 registro JSON por linha**. Isso permite processar arquivos enormes (GBs) sem carregar tudo na memória.

Para ler em Python:

```python
import json

with open("zendesk_full_export/tickets/tickets.ndjson", "r") as f:
    for line in f:
        ticket = json.loads(line)
        print(ticket["id"], ticket["subject"])
```

---

## ⚙️ Configurações opcionais

Todas as configurações podem ser ajustadas no arquivo `.env`:

| Variável              | Padrão                | Descrição                                               |
| --------------------- | --------------------- | ------------------------------------------------------- |
| `OUTPUT_DIR`          | `zendesk_full_export` | Pasta onde os arquivos são salvos                       |
| `EXPORT_COMMENTS`     | `true`                | Exportar comentários dos tickets                        |
| `COMMENTS_BATCH_SIZE` | `500`                 | Tickets processados por execução (0 = todos de uma vez) |
| `START_TIME`          | `0`                   | Unix timestamp de início (0 = tudo)                     |
| `MAX_RETRIES`         | `5`                   | Tentativas em caso de erro de rede                      |

### Dicas para contas grandes

- **Primeira execução**: considere desabilitar comentários (`EXPORT_COMMENTS=false`) para exportar os dados principais rapidamente. Depois habilite e rode novamente.
- **Comentários**: com 400k+ tickets, exportar todos os comentários pode levar **dias**. Aumente o `COMMENTS_BATCH_SIZE` para `5000` ou `0` (sem limite) se puder deixar rodando.
- **Espaço em disco**: contas grandes podem gerar vários GBs de dados. Verifique o espaço disponível antes de começar.

---

## ⏱️ Estimativas de tempo

| Dados                      | Volume   | Tempo aproximado |
| -------------------------- | -------- | ---------------- |
| Tickets (100k)             | ~400 MB  | ~30 min          |
| Tickets (400k)             | ~1.7 GB  | ~1h 15min        |
| Usuários (300k)            | ~290 MB  | ~15 min          |
| Comentários (400k tickets) | Variável | ~4-8 horas       |
| Configurações e regras     | < 1 MB   | < 1 min          |

> Os tempos variam conforme a velocidade da sua internet e os rate limits da API do Zendesk.

---

## ❓ Solução de problemas

### Erro: `command not found: python3`

O Python não está instalado. Baixe em [python.org](https://www.python.org/downloads/).

### Erro: `CREDENCIAIS NÃO CONFIGURADAS`

Você não criou o arquivo `.env` ou não preencheu as variáveis. Veja a seção [Configure suas credenciais](#3-configure-suas-credenciais).

### Erro: `403 Forbidden` em algum endpoint

Seu plano Zendesk pode não ter acesso a esse recurso (ex: SLA Policies é exclusivo do plano Enterprise). O script continua normalmente — os dados desse endpoint apenas ficam vazios.

### Erro: `429 Rate Limit`

Isso é normal! O script detecta o rate limit automaticamente, espera o tempo necessário e continua. Não é preciso fazer nada.

### A exportação caiu / fechei o terminal sem querer

Rode novamente `python3 zendesk_full_export.py` — o script retoma de onde parou usando os checkpoints salvos.

### Quero exportar só os comentários que faltam

Rode o script normalmente. Os tickets e usuários serão reprocessados rapidamente (a API incremental é eficiente), e os comentários retomam do checkpoint.

---

## 🔒 Segurança

- **Nunca compartilhe** seu arquivo `.env` ou API Token
- O `.gitignore` já está configurado para ignorar `.env` e a pasta de dados exportados
- Se acidentalmente expôs um token, **revogue imediatamente** no painel do Zendesk e gere um novo
- Os dados exportados contêm **informações sensíveis** (emails, nomes, conversas) — trate-os com cuidado

---

## 📄 Licença

MIT — veja [LICENSE](LICENSE).
