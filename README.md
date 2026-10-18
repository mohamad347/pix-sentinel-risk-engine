# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

## Como o motor de decisão funciona

O PIX SENTINEL analisa uma solicitação Pix antes da liquidação e devolve um dos
três pareceres: `approved`, `two_factor_required` ou `rejected`.

```text
Payload Pix → validação → histórico PostgreSQL → regras de risco → score → decisão
```

A política vigente, `mvp-risk-adaptive-v3-r2`, combina 28 sinais em seis
famílias independentes: exposição/velocity, perfil, relacionamento, acesso,
temporal e rede do recebedor. Cada família possui um teto para impedir que
sinais correlacionados inflem o score. O resultado final é limitado a 100.

O histórico usa janelas de 1 minuto, 5 minutos, 15 minutos, 60 minutos e 24
horas. Perfil e referência geográfica consideram somente análises anteriores
`approved`; velocity considera todas as tentativas válidas, pois uma tentativa
negada também é evidência de ataque.

A operação é idempotente: repetir o mesmo `id` e o mesmo payload devolve a
decisão persistida; reutilizar o `id` com outros dados retorna `409`. Um lock
transacional por pagador protege as contagens concorrentes. O motor não consulta
saldo, não movimenta dinheiro e não executa o segundo fator — apenas o recomenda.

## Regras

Os valores são heurísticas explicáveis para o hackathon, não probabilidades de
fraude. Dentro de exposição, perfil, relacionamento e temporal prevalece o maior
nível aplicável; acesso e rede do recebedor podem acumular pontos até o teto da
família.

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| `ELEVATED_CURRENT_VALUE` | Valor atual elevado | ≥ R$ 5.000; exposição até 25 | Uma transferência isolada de alto valor aumenta a perda potencial. |
| `MICROBURST_1M` | Muitas tentativas imediatas | ≥ 4 tentativas em 1 min; exposição até 25 | Captura automação e repetição muito rápida. |
| `ELEVATED_ATTEMPT_COUNT_5M` | Frequência elevada em curto prazo | ≥ 4 tentativas em 5 min; exposição até 25 | Detecta aceleração do comportamento transacional. |
| `ELEVATED_ATTEMPT_TOTAL_60M` | Alto volume acumulado | ≥ R$ 15.000 em 60 min; exposição até 25 | Limita fracionamento de valores em uma hora. |
| `ELEVATED_ATTEMPT_TOTAL_24H` | Alto volume diário | ≥ R$ 50.000 em 24 h; exposição até 25 | Enxerga ataques lentos fora das janelas curtas. |
| `STRUCTURED_AGGREGATE_60M` | Contagem e valor elevados juntos | ≥ 10 tentativas e ≥ R$ 15.000 em 60 min; exposição até 25 | Captura pulverização estruturada. |
| `CRITICAL_BURST_5M` | Rajada crítica de contagem e valor | ≥ 8 tentativas e ≥ R$ 30.000 em 5 min; exposição 60 | A combinação representa risco imediato suficiente para rejeição. |
| `ABOVE_PROFILE_P90` | Valor muito acima do percentil habitual | ≥ 2 × p90; perfil até 10 | Compara o Pix ao padrão individual, não apenas a um valor fixo. |
| `CRITICAL_MEDIAN_DEVIATION` | Desvio extremo da mediana | ≥ 8 × mediana; perfil até 15 | Identifica ruptura forte do comportamento habitual. |
| `ELEVATED_MEDIAN_DEVIATION` | Desvio relevante da mediana | ≥ 3 × mediana; perfil até 10 | Sinaliza mudança de padrão sem rejeitar isoladamente. |
| `NEW_COUNTERPARTY` | Destinatário novo para o pagador | ≥ 3 tentativas anteriores e contraparte não observada; relacionamento até 5 | Destinos novos têm menos confiança histórica. |
| `PAIR_REPETITION_15M` | Repetição do mesmo par | ≥ 5 tentativas em 15 min; relacionamento até 10 | Pode indicar retentativa automatizada ou coerção. |
| `CRITICAL_FAN_OUT_60M` | Muitos destinatários diferentes | ≥ 8 em 60 min; relacionamento até 15 | É compatível com dispersão rápida de recursos. |
| `ELEVATED_FAN_OUT_60M` | Diversidade elevada de destinatários | ≥ 4 e < 8 em 60 min; relacionamento até 12 | Antecipa dispersão antes do nível crítico. |
| `NEW_DEVICE` | Dispositivo não observado para o pagador | ≥ 3 tentativas anteriores; acesso +10 | É um indício de troca de posse ou tomada de conta. |
| `NEW_IP` | IP não observado para o pagador | ≥ 3 tentativas anteriores; acesso +8 | Acrescenta contexto independente ao dispositivo. |
| `CHANNEL_CHANGE` | Canal não observado para o pagador | ≥ 3 tentativas anteriores; acesso +5 | Mudança simultânea de canal reforça anomalia de acesso. |
| `DEVICE_CHURN_60M` | Troca rápida de dispositivos | ≥ 3 dispositivos em 60 min; acesso pelo menos 15 | Sessões legítimas raramente alternam tantos dispositivos em uma hora. |
| `IP_CHURN_60M` | Troca rápida de IPs | ≥ 3 IPs em 60 min; acesso pelo menos 12 | Pode indicar proxy, automação ou sequestro de sessão. |
| `DEVICE_SHARED_ACROSS_PAYERS_60M` | Mesmo dispositivo em vários pagadores | ≥ 3 pagadores em 60 min; acesso pelo menos 15 | Identifica infraestrutura compartilhada por possível fraudador. |
| `IP_SHARED_ACROSS_PAYERS_60M` | Mesmo IP em vários pagadores | ≥ 5 pagadores em 60 min; acesso pelo menos 8 | Ajuda a detectar origem coordenada sem bloquear redes compartilhadas isoladamente. |
| `TRANSACTION_CLOCK_SKEW` | Horário declarado distante do recebimento | diferença ≥ 2 h; temporal até 8 | Detecta payload atrasado, adulterado ou reenviado. |
| `UNUSUAL_NIGHTTIME` | Operação noturna fora do perfil | 00h–06h UTC, ≥ 5 amostras e histórico noturno ≤ 20%; temporal até 5 | Usa horário somente quando ele realmente foge do hábito do pagador. |
| `ELEVATED_GEO_VELOCITY` | Deslocamento geográfico improvável | distância ≥ 100 km e velocidade de 300 a 1.000 km/h; temporal até 10 | Acrescenta fricção sem tratar viagem plausível como fraude. |
| `IMPOSSIBLE_GEO_VELOCITY` | Deslocamento geográfico impossível | distância ≥ 100 km e velocidade > 1.000 km/h, ou em até 60 s; temporal 25 | Sugere uso simultâneo ou localização incompatível, mas não rejeita sozinho. |
| `RECIPIENT_FIRST_SYSTEM_OBSERVATION` | Recebedor nunca visto pelo motor | primeira observação global; rede +5 | Um destino sem histórico local merece cautela moderada. |
| `RECIPIENT_FAN_IN_60M` | Recebedor concentra muitos pagadores | ≥ 5 pagadores e ≥ 8 tentativas anteriores em 60 min; rede 15 | Pode representar conta coletora ou destinatário coordenado. |
| `YOUNG_DESTINATION_ACCOUNT` | Conta recebedora recente recebe valor alto | idade de 0 a 6 dias e valor ≥ R$ 3.000; rede +5 | Contas novas de destino apresentam pouco histórico de confiança. |

Tetos por família: exposição/velocity 60, perfil 15, relacionamento 15, acesso
20, temporal 25 e rede do recebedor 20. O perfil exige ao menos cinco análises
anteriores aprovadas, histórico de 24 horas, mediana positiva e p90 positivo.

Device, IP e geolocalização vêm do payload e não provam identidade. As
coordenadas são usadas para calcular distância e velocidade, mas não são
copiadas para o snapshot de evidências.

## Como as regras viram decisão

As salvaguardas são avaliadas nesta ordem:

| Prioridade | Condição | Decisão |
|---:|---|---|
| 1 | `CRITICAL_BURST_5M` | `rejected` |
| 2 | `IMPOSSIBLE_GEO_VELOCITY` e score de acesso ≥ 10 | `rejected` |
| 3 | score total ≥ 60, pelo menos 4 famílias ativas e acesso ≥ 18 ou rede do recebedor ≥ 15 | `rejected` |
| 4 | nenhuma salvaguarda de rejeição e score total ≥ 25 | `two_factor_required` |
| 5 | nenhuma salvaguarda de rejeição e score total < 25 | `approved` |

Uma velocidade impossível isolada, por exemplo, soma 25 e pede 2FA; para
rejeitar, ela precisa coincidir com anomalia independente de acesso. Da mesma
forma, alcançar 60 pontos não basta: a rejeição por convergência exige quatro
famílias e um sinal forte de acesso ou da rede do recebedor.

Quando a decisão é `two_factor_required`, o motor recomenda:

| 2FA | Quando |
|---|---|
| `push_confirmation` (`standard`) | Risco suficiente para fricção, sem evidência forte de tomada de conta. |
| `facial_liveness` (`enhanced`) | Acesso ≥ 18 combinado com perfil ≥ 10, relacionamento ≥ 12 ou temporal ≥ 15. |

O 2FA tem status `recommended`: sua execução e confirmação pertencem ao
orquestrador bancário. `approved` não autoriza a liquidação e `rejected` não é
prova de fraude.

## Como rodar e testar

Pré-requisito: Docker com Compose.

Suba a API e o PostgreSQL:

```powershell
docker compose up --build -d
docker compose ps
```

A API fica em `http://localhost:3000`. `GET /health_check` é público; as rotas
Pix usam o header `INTERNAL-TOKEN: default_token`:

- `POST /pix/transaction` analisa e persiste uma tentativa;
- `GET /pix/transaction/{id}` recupera a decisão original sem recalcular.

Para instalar as dependências de desenvolvimento e executar os testes unitários:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests/unit
```

Para a suíte completa, use o PostgreSQL descartável e isolado:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build -d

$env:APP_ENV = "test"
$env:DATABASE_URL = "postgresql+psycopg2://pix_test:pix_test@localhost:55432/pix_sentinel_test"
$env:PIX_INTEGRATION_TESTS = "1"
$env:SERVER_LOCALHOST = "localhost"
$env:API_PORT = "3300"
$env:INTERNAL_TOKEN = "pix-test-token"

.\.venv\Scripts\python.exe -m pytest -q
```

Validação mais recente: `117 passed` nos testes unitários e `147 passed, 9
skipped` na suíte completa com PostgreSQL isolado.

Ao terminar, remova apenas o ambiente de teste:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml down -v
```
