# Como o PIX SENTINEL é organizado

Este documento descreve o repositório como ele está implementado em
18/10/2026. O PIX SENTINEL é uma API FastAPI com PostgreSQL que recebe uma
tentativa de Pix, produz um parecer antifraude explicável e persiste a decisão.
Ele não consulta saldo, não executa o Pix e não realiza o segundo fator.

## Visão geral

```text
requisição HTTP
  → contexto e correlação
  → log e autenticação interna
  → sessão de banco
  → resource e JSON Schema
  → controller e transação
  → repository e PostgreSQL
  → política antifraude
  → DTO e resposta HTTP
```

O caminho de negócio possui duas rotas: `POST /pix/transaction`, que avalia e
persiste uma tentativa, e `GET /pix/transaction/{id}`, que recupera o snapshot
original pelo `id` externo. As rotas `/` e `/health_check` são apenas de
infraestrutura.

## Estrutura do repositório

| Caminho | Responsabilidade atual |
|---|---|
| `src/app.py` | Cria a aplicação, registra os quatro middlewares, as quatro rotas e os tratadores de erro. |
| `src/resources/` | Traduz HTTP para chamadas de controller e define o status de sucesso. |
| `src/schemas/` | Contém o JSON Schema da entrada do POST. |
| `src/controllers/pix_transaction_controller.py` | Valida semântica, controla idempotência, lock, histórico, política, persistência e commit. |
| `src/controllers/pix_adaptive_risk_policy.py` | Implementa a política padrão `mvp-risk-adaptive-v3-r5`. |
| `src/controllers/pix_policy_selector.py` | Seleciona R5 por padrão; mantém V1 selecionável e V2/V2 R2 restritas a teste. |
| `src/repositories/` | Executa locks, agregações históricas e operações SQLAlchemy. |
| `src/models/` | Mapeia `pix_transactions` e `pix_analyses`. |
| `src/dtos/` | Limita a saída pública a `key`, `status` e `metadata`. |
| `src/utils/pix_context.py` | Deriva contexto de perfil, acesso, horário, geolocalização e rede. |
| `src/utils/pix_history.py` | Monta o snapshot histórico e a chave do advisory lock. |
| `src/utils/pix_canonicalization.py` | Canoniza o contrato oficial e gera o SHA-256 usado na idempotência. |
| `src/errors/` | Converte erros previstos e inesperados no envelope HTTP do projeto. |
| `src/middlewares/` | Mantém request ID, logs, token interno e ciclo de vida da sessão. |
| `database/` | Schema para instalações novas e SQL incremental para bases existentes. |
| `tests/` | Testes unitários e integrações reais com PostgreSQL. |
| `scripts/` | Matriz de testes, demonstração, avaliações sintéticas e benchmark. |

`src/connectors/` pertence ao template base e não participa do fluxo antifraude
Pix atual.

## Separação de responsabilidades

O resource valida a estrutura pelo JSON Schema e chama o controller. O
controller valida regras que o schema não representa com segurança, como
inteiro estrito e timestamp com fuso, e define o limite transacional completo.
O repository recebe a sessão já associada à requisição; ele não cria sessão nem
faz commit. A política recebe somente dados extraídos e evidências derivadas,
sem acessar HTTP ou banco diretamente.

O commit ocorre no controller depois de criar a tentativa e a análise. O
middleware de sessão executa rollback quando uma exceção escapa e fecha a
sessão sempre. Como o SQLAlchemy usa `autoflush=False`, o repository usa
`flush()` para obter a chave interna antes de criar a análise, sem tornar a
linha visível a outras transações antes do commit.

## Concorrência e histórico

Cada novo Pix adquire `pg_advisory_xact_lock` sobre uma chave SHA-256 derivada
de documento, agência e conta do pagador. O lock é liberado pelo PostgreSQL no
commit ou rollback e possui timeout local de 1 segundo. Depois do lock, o
servidor obtém `received_at` com `clock_timestamp()`, insere a tentativa e faz
as agregações.

As janelas são `(T − W, T]` e incluem a tentativa atual exatamente uma vez. As
consultas de “anterior” excluem sua chave interna. Perfil de valor e referência
geográfica usam somente decisões anteriores `approved`; velocity, familiaridade
de device/IP/canal e rede usam tentativas locais válidas independentemente do
parecer.

## Política ativa

A variável `PIX_RISK_POLICY` seleciona a política. Sem configuração explícita,
a aplicação usa `mvp-risk-adaptive-v3-r5`, formada por 28 sinais agrupados em
seis famílias com teto próprio. O score é limitado a 100, mas rejeição não
depende apenas dele: há salvaguardas para rajada crítica, viagem impossível com
anomalia de acesso e convergência de múltiplas famílias. O README contém a
tabela completa das regras.

V1 (`mvp-risk-experimental-v1`) permanece disponível para compatibilidade. V2
e V2 R2 só podem ser selecionadas com `APP_ENV=test`; elas existem para a
matriz comparativa, não como configuração operacional.

## Como executar

API e PostgreSQL de desenvolvimento:

```powershell
docker compose up --build -d --wait
```

Matriz completa, com banco descartável e falha se houver teste pulado:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from pix_test_runner
```

O executor alterna R5, V1, V2 e V2 R2 e limpa o PostgreSQL entre as suítes. O
resultado validado mais recente é `176 passed, 0 skipped`.

## Documentação mantida

| Documento | Assunto |
|---|---|
| `como-o-projeto-e-organizado.md` | Camadas, responsabilidades e execução. |
| `PIX_SENTINEL_RFC_v1 (1).md` | Decisões arquiteturais e de produto. |
| `PIX_SENTINEL_ERD_v1 (1).md` | Modelo persistente e índices reais. |
| `PIX_SENTINEL_FLUXOS_v1 (1).md` | Fluxos de POST, GET, decisão, replay e falha. |
| `PIX_SENTINEL_ROTAS_v1 (2).md` | Contrato HTTP efetivamente implementado. |
| `PIX_SENTINEL_ESTATISTICAS_TEMPO_API.md` | Metodologia e resultados do benchmark local. |
