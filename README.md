# PIX SENTINEL

**Integrantes:** João Vitor Miranda Sousa e Mohamad Ahmad Barakat

## Como o motor de decisão funciona

O PIX SENTINEL analisa uma solicitação Pix antes da liquidação e devolve um dos
três pareceres: `approved`, `two_factor_required` ou `rejected`.

```text
Payload Pix → validação → histórico PostgreSQL → regras de risco → score → decisão
```

A política vigente, `mvp-risk-adaptive-v3-r4`, combina 29 sinais em seis
famílias independentes: exposição/velocity, perfil, relacionamento, acesso,
temporal e rede do recebedor. Cada família possui um teto para impedir que
sinais correlacionados inflem o score. O resultado final é limitado a 100.

O histórico usa janelas de 1 minuto, 5 minutos, 15 minutos, 60 minutos e 24
horas. Perfil e referência geográfica consideram somente análises anteriores
`approved`; velocity considera todas as tentativas válidas, pois uma tentativa
negada também é evidência de ataque.

As janelas incluem a tentativa atual exatamente uma vez. Já expressões como
“destinatário nunca visto” consultam apenas eventos anteriores. Assim, o oitavo
evento dispara um limiar de oito sem deixar de distinguir primeira observação.

A operação é idempotente: repetir o mesmo `id` e o mesmo payload devolve a
decisão persistida; reutilizar o `id` com outros dados retorna `409`. Um lock
transacional por pagador protege as contagens concorrentes. O motor não consulta
saldo, não movimenta dinheiro e não executa o segundo fator — apenas o recomenda.

## Regras

Os valores são heurísticas explicáveis para o hackathon, não probabilidades de
fraude. Dentro de exposição, perfil, relacionamento e temporal prevalece o maior
nível aplicável; acesso e rede do recebedor podem acumular pontos até o teto da
família.

Na dimensão de frequência, os níveis são mutuamente exclusivos: quando a
microrrajada de 1 minuto ocorre, a regra mais fraca de 5 minutos não é repetida.
O mesmo vale para estruturação versus simples total elevado em 60 minutos.
Essa escada produz severidade crescente e explicável: frequência em 5 minutos
vale 15, microrrajada vale 25, estruturação vale 35 e rajada crítica vale 60.

| Regra | Lógica | Parâmetros | Por quê |
|---|---|---|---|
| `ELEVATED_CURRENT_VALUE` | Valor atual elevado por segmento | PF/indefinido ≥ R$ 5.000; PJ ≥ R$ 15.000; define exposição em 25 | Evita tratar o fluxo financeiro típico de uma empresa como se fosse o de uma pessoa física. |
| `MICROBURST_1M` | Muitas tentativas praticamente simultâneas | PF/indefinido ≥ 4; PJ ≥ 6 tentativas em 1 min; define exposição em 25 | É a aceleração mais forte: sozinha já justifica 2FA, com tolerância maior para lotes PJ. |
| `ELEVATED_ATTEMPT_COUNT_5M` | Frequência elevada, mas menos concentrada | PF/indefinido ≥ 4; PJ ≥ 8 tentativas em 5 min; eleva exposição para pelo menos 15 | É um alerta combinatório; isolado não cria fricção e não duplica a microrrajada de 1 min. |
| `ELEVATED_ATTEMPT_TOTAL_60M` | Alto volume acumulado | PF/indefinido ≥ R$ 15.000; PJ ≥ R$ 50.000 em 60 min; define exposição em 25 | Limita fracionamento sem penalizar da mesma forma o giro empresarial. |
| `ELEVATED_ATTEMPT_TOTAL_24H` | Alto volume diário | PF/indefinido ≥ R$ 50.000; PJ ≥ R$ 150.000 em 24 h; define exposição em 25 | Enxerga ataques lentos fora das janelas curtas. |
| `STRUCTURED_AGGREGATE_60M` | Contagem e valor elevados juntos | ≥ 10 tentativas e total de 60 min do segmento; define exposição em 35 | É mais grave que somente contagem ou somente valor, mas permanece abaixo da rajada crítica. |
| `CRITICAL_BURST_5M` | Rajada crítica de contagem e valor | ≥ 8 tentativas e PF/indefinido ≥ R$ 30.000 ou PJ ≥ R$ 100.000 em 5 min; exposição 60 | A combinação representa risco imediato suficiente para rejeição. |
| `FIRST_OBSERVED_YOUNG_SOURCE_ACCOUNT` | Cold start material em conta de origem recente | primeira observação local, conta com menos de 30 dias e PF/indefinido ≥ R$ 3.000 ou PJ ≥ R$ 10.000; perfil 15 e 2FA determinístico | Compensa a ausência de perfil sem chamar todo cliente novo de fraude. |
| `ABOVE_PROFILE_P90` | Valor muito acima do percentil habitual | ≥ 2 × p90; eleva perfil para pelo menos 10 | Compara o Pix ao padrão individual, não apenas a um valor fixo. |
| `CRITICAL_MEDIAN_DEVIATION` | Desvio extremo da mediana | ≥ 8 × mediana; define perfil em 15 | Identifica ruptura forte do comportamento habitual. |
| `ELEVATED_MEDIAN_DEVIATION` | Desvio relevante da mediana | ≥ 3 × mediana; eleva perfil para pelo menos 10 | Sinaliza mudança de padrão sem rejeitar isoladamente. |
| `NEW_COUNTERPARTY` | Destinatário novo para o pagador | ≥ 3 tentativas anteriores e contraparte não observada; eleva relacionamento para pelo menos 5 | Destinos novos têm menos confiança histórica. |
| `PAIR_REPETITION_15M` | Repetição do mesmo par | ≥ 5 tentativas em 15 min; eleva relacionamento para pelo menos 10 | Pode indicar retentativa automatizada ou coerção. |
| `CRITICAL_FAN_OUT_60M` | Muitos destinatários diferentes | PF/indefinido ≥ 8; PJ ≥ 15 em 60 min; define relacionamento em 15 | Considera que folha e fornecedores tornam a dispersão mais comum em PJ. |
| `ELEVATED_FAN_OUT_60M` | Diversidade elevada de destinatários | PF/indefinido ≥ 4; PJ ≥ 8, abaixo do crítico; relacionamento pelo menos 12 | Antecipa dispersão sem equiparar atividade empresarial à pessoal. |
| `NEW_DEVICE` | Dispositivo não observado para o pagador | ≥ 3 tentativas anteriores; acesso +10 | É um indício de troca de posse ou tomada de conta. |
| `NEW_IP` | IP não observado para o pagador | ≥ 3 tentativas anteriores; acesso +8 | Acrescenta contexto independente ao dispositivo. |
| `CHANNEL_CHANGE` | Canal não observado para o pagador | ≥ 3 tentativas anteriores; acesso +5 | Mudança simultânea de canal reforça anomalia de acesso. |
| `DEVICE_CHURN_60M` | Troca rápida de dispositivos | ≥ 3 dispositivos em 60 min; acesso pelo menos 15 | Sessões legítimas raramente alternam tantos dispositivos em uma hora. |
| `IP_CHURN_60M` | Troca rápida de IPs | ≥ 3 IPs em 60 min; acesso pelo menos 12 | Pode indicar proxy, automação ou sequestro de sessão. |
| `DEVICE_SHARED_ACROSS_PAYERS_60M` | Mesmo dispositivo em vários pagadores | ≥ 3 pagadores em 60 min; acesso pelo menos 15 | Identifica infraestrutura compartilhada por possível fraudador. |
| `IP_SHARED_ACROSS_PAYERS_60M` | Mesmo IP em vários pagadores | ≥ 5 pagadores em 60 min; acesso pelo menos 8 | Ajuda a detectar origem coordenada sem bloquear redes compartilhadas isoladamente. |
| `TRANSACTION_CLOCK_SKEW` | Horário declarado distante do recebimento | diferença ≥ 2 h; eleva temporal para pelo menos 8 | Detecta payload atrasado, adulterado ou reenviado. |
| `UNUSUAL_NIGHTTIME` | Operação noturna fora do perfil | 20h–06h em `America/Sao_Paulo`, ≥ 5 amostras e histórico noturno ≤ 20%; eleva temporal para pelo menos 5 | Usa o período noturno de referência do Pix, mas só pontua quando ele foge do hábito do pagador. |
| `ELEVATED_GEO_VELOCITY` | Deslocamento geográfico improvável | distância ≥ 100 km e velocidade de 300 a 1.000 km/h; eleva temporal para pelo menos 10 | Acrescenta fricção sem tratar viagem plausível como fraude. |
| `IMPOSSIBLE_GEO_VELOCITY` | Deslocamento geográfico impossível | distância ≥ 100 km e velocidade > 1.000 km/h, ou em até 60 s; temporal 25 | Sugere uso simultâneo ou localização incompatível, mas não rejeita sozinho. |
| `RECIPIENT_FIRST_SYSTEM_OBSERVATION` | Recebedor nunca visto pelo motor | primeira observação global; rede +5 | Um destino sem histórico local merece cautela moderada. |
| `RECIPIENT_FAN_IN_60M` | Recebedor concentra muitos pagadores | recebedor PF/indefinido: ≥ 5 pagadores e ≥ 8 tentativas; PJ: ≥ 15 e ≥ 25 em 60 min; rede 15 | Reduz falso positivo em comércios PJ sem neutralizar concentração em conta pessoal. |
| `YOUNG_DESTINATION_ACCOUNT` | Conta recebedora recente recebe valor alto | idade de 0 a 6 dias e PF/indefinido ≥ R$ 3.000 ou PJ ≥ R$ 10.000; rede +5 | Contas novas de destino apresentam pouco histórico de confiança. |

Tetos por família: exposição/velocity 60, perfil 15, relacionamento 15, acesso
20, temporal 25 e rede do recebedor 20. O perfil exige ao menos cinco análises
anteriores aprovadas, histórico de 24 horas, mediana positiva e p90 positivo.

Device, IP e geolocalização vêm do payload e não provam identidade. As
coordenadas são usadas para calcular distância e velocidade, mas não são
copiadas para o snapshot de evidências.

A janela 20h–06h segue o período noturno padrão de referência do Pix. Ela não
simula nem aplica o limite bancário individual do cliente, ausente do contrato:
é apenas um sinal comportamental fraco, de 5 pontos, e só aparece quando no
máximo 20% do histórico aprovado ocorreu nesse período.

PF e PJ são inferidos apenas pelo formato normalizado do documento. Valores e
dispersão usam limiares segmentados; perfil histórico, acesso e geolocalização
continuam com a mesma lógica. Em transferência entre documentos iguais, o motor
não pontua novidade do destinatário nem conta recebedora jovem.

## Como as regras viram decisão

As salvaguardas são avaliadas nesta ordem:

| Prioridade | Condição | Decisão |
|---:|---|---|
| 1 | `CRITICAL_BURST_5M` | `rejected` |
| 2 | `IMPOSSIBLE_GEO_VELOCITY` e score de acesso ≥ 10 | `rejected` |
| 3 | score total ≥ 60, pelo menos 4 famílias ativas e acesso ≥ 18 ou rede do recebedor ≥ 15 | `rejected` |
| 4 | primeira observação local, conta de origem < 30 dias e valor material do segmento | `two_factor_required` |
| 5 | acesso ≥ 18 junto de relacionamento ou rede do recebedor com risco | `two_factor_required` |
| 6 | nenhuma salvaguarda anterior e score total ≥ 25 | `two_factor_required` |
| 7 | nenhuma salvaguarda anterior e score total < 25 | `approved` |

Essa quinta salvaguarda cobre o caso em que device e IP são novos e o destino
também é novo: o score é 23, mas a convergência de acesso e relacionamento pede
confirmação em vez de aprovar silenciosamente. Uma velocidade impossível isolada,
por exemplo, soma 25 e pede 2FA; para
rejeitar, ela precisa coincidir com anomalia independente de acesso. Da mesma
forma, alcançar 60 pontos não basta: a rejeição por convergência exige quatro
famílias e um sinal forte de acesso ou da rede do recebedor.

Quando a decisão é `two_factor_required`, o motor recomenda:

| 2FA | Quando |
|---|---|
| `push_confirmation` (`standard`) | Risco suficiente para fricção, sem evidência forte de tomada de conta. |
| `facial_liveness` (`enhanced`) | Somente PF: acesso ≥ 18 combinado com perfil ≥ 10, relacionamento ≥ 12 ou temporal ≥ 15. |

Para PJ e tipo indefinido, o motor nunca recomenda uma “face da empresa”:
mesmo no contexto reforçado, utiliza `push_confirmation` e deixa a autenticação
do operador autorizado para o orquestrador bancário.

O 2FA tem status `recommended`: sua execução e confirmação pertencem ao
orquestrador bancário. `approved` não autoriza a liquidação e `rejected` não é
prova de fraude.

### Exemplo concreto de cálculo

Considere um Pix PF de R$ 6.000 que seja a quarta tentativa em cinco minutos,
feito de um dispositivo e IP novos para um destinatário novo:

```text
Exposição
  valor ≥ R$ 5.000 .............. 25
  4 tentativas em 5 min ......... 15
  resultado da família .......... 25  (usa o maior; não soma 25 + 15)

Acesso
  dispositivo novo .............. 10
  IP novo .......................  8
  resultado da família .......... 18  (soma, respeitando o teto 20)

Relacionamento
  destinatário novo .............  5

Score total = 25 + 18 + 5 = 48
Decisão     = two_factor_required
2FA         = push_confirmation
```

O motor não soma livremente os dois sinais de exposição porque ambos descrevem
o mesmo fenômeno: concentração de risco transacional. Já acesso e relacionamento
são evidências diferentes e entram separadamente no score.

O racional completo, incluindo alternativas avaliadas, fórmula, salvaguardas e
limitações, está em
[Método de decisão e explicabilidade](docs/PIX_SENTINEL_METODO_DECISAO_E_EXPLICABILIDADE.md).

## Como rodar e testar

Pré-requisito: Docker com Compose.

Suba a API e o PostgreSQL:

```powershell
docker compose up --build -d --wait
docker compose ps
```

A API fica em `http://localhost:3000`. `GET /health_check` é público; as rotas
Pix usam o header `INTERNAL-TOKEN: default_token`:

- `POST /pix/transaction` analisa e persiste uma tentativa;
- `GET /pix/transaction/{id}` recupera a decisão original sem recalcular.

Para executar toda a suíte em API e PostgreSQL descartáveis, sem instalar Python
nem configurar variáveis na máquina:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from pix_test_runner
```

O serviço `pix_test_runner` espera banco e API ficarem saudáveis, executa o
`pytest` dentro do Docker e devolve seu código de saída. Banco e API de teste se
comunicam somente pela rede interna do Compose, sem disputar portas da máquina.

Validação mais recente: `135 passed` nos testes unitários e `167 passed, 9
skipped` na suíte completa com PostgreSQL isolado.

Ao terminar, remova apenas o ambiente de teste:

```powershell
docker compose -p pix-sentinel-tests -f docker-compose.test.yml down -v
```
