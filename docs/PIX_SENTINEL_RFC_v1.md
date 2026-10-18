# RFC — PIX SENTINEL: motor antifraude Pix

| Campo | Valor |
|---|---|
| Time | João Vitor Miranda Sousa e Mohamad Ahmad Barakat |
| Atualização | 18/10/2026 |
| Estado | Implementado; política R5 vigente e calibrável |
| Política padrão | `mvp-risk-adaptive-v3-r5` |

## Contexto e objetivo

O serviço precisa emitir, antes da liquidação, um parecer rápido e explicável
para uma tentativa Pix: `approved`, `two_factor_required` ou `rejected`. Valor
isolado é insuficiente; frequência, perfil, acesso, horário, localização,
relacionamento e comportamento do recebedor mudam a interpretação. Ao mesmo
tempo, nenhum sinal declarado pelo cliente prova fraude.

O objetivo é um motor determinístico e auditável, adequado a dados sem rótulos
reais: regras e limiares ficam explícitos, sinais correlacionados têm teto e
rejeições exigem situações críticas ou convergência. O motor registra o parecer
original para garantir reprodutibilidade em GET e replay.

## Escopo

Incluído:

- contrato oficial de entrada e saída;
- validação estrutural e semântica;
- idempotência por ID externo e fingerprint canônico;
- histórico local no PostgreSQL e janelas deslizantes;
- segmentação PF/PJ por formato do documento;
- score por famílias, salvaguardas de decisão e recomendação de 2FA;
- persistência atômica, concorrência por pagador e consulta do snapshot;
- testes unitários, integração isolada e benchmark reproduzível.

Fora do escopo:

- saldo, limite bancário, estado cadastral ou core simulado;
- execução, reserva ou liquidação do Pix;
- execução e validação de push, liveness ou autenticação do operador;
- reputação externa de IP/device/beneficiário;
- modelo de ML treinado ou afirmação de fraude comprovada.

## Decisões arquiteturais

### ADR-01 — política determinística por famílias

A R5 contém 28 sinais em seis famílias: exposição/velocity, perfil,
relacionamento, acesso, temporal e rede do recebedor. Cada família possui teto;
o score final é limitado a 100. Isso evita somar várias descrições do mesmo
fenômeno como se fossem evidências independentes.

Escolhemos regras determinísticas porque o projeto não dispõe de base rotulada
representativa para treinar e validar ML. A abordagem permite explicar cada
decisão, testar fronteiras e alterar parâmetros sem esconder o raciocínio. Os
limiares são heurísticas de hackathon, não probabilidades calibradas.

### ADR-02 — fricção proporcional e rejeição protegida

Score abaixo de 25 aprova; a partir de 25 recomenda 2FA. Score alto sozinho não
rejeita. Rejeição ocorre por uma das três salvaguardas:

1. oito tentativas e volume crítico do segmento em cinco minutos;
2. deslocamento impossível combinado com anomalia de acesso;
3. score ≥ 60, quatro famílias ativas e acesso ou rede do recebedor forte.

Há ainda salvaguardas de 2FA para cold start material e para forte anomalia de
acesso combinada com relação/rede. Essa ordem reduz rejeição por um único dado
declarado ou por coincidência legítima.

### ADR-03 — histórico local, com perfis aprovados

Velocity considera todas as tentativas válidas, inclusive as rejeitadas, pois
retentativa é evidência do ataque. Mediana, p90, hábito noturno e referência
geográfica usam somente análises anteriores `approved`, reduzindo contaminação
do perfil por eventos suspeitos. Familiaridade de device, IP, canal e
contraparte continua baseada no histórico local completo por decisão de
simplicidade do MVP.

“Novo” significa não observado pelo PIX SENTINEL, não novo no banco ou na vida
do cliente. A primeira tentativa não é punida isoladamente; somente uma conta
de origem declarada com menos de 30 dias e valor material exige 2FA.

### ADR-04 — segmentação PF/PJ

O tipo é inferido pelo documento normalizado: 11 dígitos para PF, 14 caracteres
alfanuméricos para PJ e, em outros casos, `unknown`. PJ recebe limiares maiores
para valor, microrrajada, volume, fan-out, fan-in e contas jovens. `unknown`
usa limiares conservadores de PF.

`facial_liveness` só é recomendada para PF e em risco compatível com tomada de
conta. PJ e tipo indefinido recebem `push_confirmation`; a autenticação do
representante fica fora do motor.

### ADR-05 — idempotência com snapshot imutável

`external_id` é único. Um SHA-256 versionado representa os campos oficiais
canonizados: timestamps equivalentes viram o mesmo instante UTC e geo ausente é
igual a `null`. Replay idêntico retorna a análise gravada; conteúdo oficial
divergente retorna 409. A política não é reexecutada em replay ou GET.

Campos extras são aceitos e persistidos pelo contrato atual, mas não participam
da identidade de negócio. Essa compatibilidade é deliberada e deve ser
reavaliada se extras passarem a ter semântica antifraude.

### ADR-06 — duas tabelas e commit único

Uma tabela preserva a tentativa e alimenta o histórico; a outra preserva o
parecer e as evidências da política. A separação evita misturar contrato de
entrada com resultado e mantém a análise recuperável. As duas linhas são
gravadas numa única transação; o HTTP 200 só é devolvido depois do commit.

### ADR-07 — serialização por pagador

Um `pg_advisory_xact_lock` derivado da identidade local do pagador é adquirido
antes do timestamp, insert e agregações. Isso faz Pix distintos do mesmo
pagador enxergarem os commits anteriores em ordem e evita contagem histórica
desatualizada. O custo é espera em rajadas do mesmo pagador; o timeout local é
1 segundo.

### ADR-08 — horário e geovelocidade

O período noturno é calculado em `America/Sao_Paulo`, das 20h às 06h, e só
pontua após cinco observações aprovadas quando no máximo 20% ocorreram à noite.
A distância usa Haversine entre a posição atual e a última posição aprovada; a
velocidade é distância pelo tempo de servidor. Acima de 1.000 km/h, ou 100 km
em até 60 segundos, o sinal é crítico, mas só rejeita junto de acesso anômalo.

## Contrato público resumido

| Método | Rota | Resultado |
|---|---|---|
| `POST` | `/pix/transaction` | Valida, avalia, persiste e retorna `key`, `status`, `metadata`. |
| `GET` | `/pix/transaction/{id}` | Recupera os mesmos campos pelo ID externo, sem recalcular. |

Uma decisão `rejected` continua sendo HTTP 200: a análise terminou com sucesso.
Erros de contrato, conflito e infraestrutura usam códigos HTTP separados. As
rotas Pix requerem `INTERNAL-TOKEN`; toda resposta traz `X-Request-ID`.

## Alternativas consideradas

| Alternativa | Motivo de não adoção no MVP |
|---|---|
| Apenas valor fixo | Ignora comportamento e produz falsos positivos em PJ. |
| Soma livre de todas as regras | Duplica evidências correlacionadas e infla o score. |
| Score alto rejeita automaticamente | Um conjunto de sinais fracos poderia bloquear uma operação legítima. |
| ML supervisionado | Não há rótulos reais, holdout independente ou calibração suficiente. |
| Mock/core bancário | Mistura antifraude com saldo, elegibilidade e execução financeira. |
| Histórico sem lock | Pix concorrentes do mesmo pagador poderiam usar a mesma visão antiga. |
| Geo impossível rejeita isoladamente | Coordenada é declarada pelo cliente e pode estar imprecisa ou adulterada. |

## Evidência de validação

- matriz Docker: `176 passed, 0 skipped`, cobrindo R5, V1, V2 e V2 R2;
- PostgreSQL real e descartável entre suítes;
- benchmark local do POST: p95 de 17,39 ms com um worker e 137,96 ms com oito,
  em 600 requisições medidas e todas HTTP 200;
- cenários e testes de contrato, replay, concorrência, rollback, PF/PJ,
  geovelocidade, rede, cold start e desafios 2FA.

As medições são evidência do ambiente local, não SLA nem calibração de eficácia
contra fraude real.

## Riscos e evolução

- Limiar heurístico exige validação com dados rotulados e monitoramento de falso
  positivo/falso negativo antes de produção.
- PF/PJ é inferido pelo formato de um documento declarado, sem validação
  cadastral.
- IP, device, canal, datas de abertura e geolocalização não são atestados.
- O histórico representa tentativas recebidas por esta implantação, não todo o
  relacionamento bancário.
- O lock é específico de PostgreSQL e timeout/falha de banco ainda retornam 500
  genérico.
- Campos extras persistidos aumentam superfície de dados; produção deve definir
  allowlist, retenção, criptografia e acesso.
- Ainda faltam teste de carga de longa duração, base grande, múltiplas réplicas e
  contenção extrema do mesmo pagador.

As regras completas estão no README, o contrato detalhado está em
`PIX_SENTINEL_ROTAS_v1 (2).md`, e as métricas em
`PIX_SENTINEL_ESTATISTICAS_TEMPO_API.md`.
