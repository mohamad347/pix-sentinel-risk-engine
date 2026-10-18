# PIX SENTINEL — simulações e defesa da política antifraude

**Data da validação:** 18/10/2026  
**Política executada:** `mvp-risk-adaptive-v3-r9`

## O que este documento comprova

Os casos abaixo são executados pelo motor real com
`python scripts/evaluate_adaptive_v3.py`. Eles caracterizam comportamento e
fronteiras; não constituem base rotulada, acurácia ou taxa de fraude.

O score-base soma seis famílias com teto próprio. Quando uma tipologia
determinística exige 2FA, o score operacional recebe piso 25:

```text
base = min(100, exposição + perfil + relacionamento + acesso + temporal + recebedor)
score = max(base, 25) somente quando uma salvaguarda determinística de 2FA dispara
```

Sinais correlacionados da mesma família usam o maior nível aplicável. Por
exemplo, valor alto e microrrajada não são somados livremente.

## Resultado das 22 situações

| Caso | Situação | Famílias E/P/R/A/T/B | Score | Resultado |
|---|---|---:|---:|---|
| A01 | Rotina habitual | `0/0/0/0/0/0` | 0 | `approved` |
| A02 | R$ 5 mil em primeira observação de conta com 5 dias | `10/15/0/0/0/0` | 25 | `two_factor_required` |
| A03 | PF: 12 tentativas e R$ 50 mil em 5 min | `60/15/0/0/0/0` | 75 | `rejected` |
| A04 | PF: 20 tentativas e R$ 50 mil em 24 h | `25/0/0/0/0/0` | 25 | `two_factor_required` |
| A05 | Acesso anômalo, fan-out e 1.800 km/h | `0/0/12/20/25/0` | 57 | `rejected` |
| A06 | Fan-in, conta jovem, IP compartilhado e relógio divergente | `0/0/0/8/8/20` | 36 | `two_factor_required` |
| A07 | Valor 30 vezes a mediana, novo destino, device e IP novos | `0/15/12/18/0/0` | 45 | `two_factor_required` |
| A08 | PJ: lote habitual de 8 × R$ 4 mil em 5 min | `0/0/0/0/0/0` | 0 | `approved` |
| A09 | Perfil, fan-out, acesso e fan-in críticos | `0/15/15/20/0/15` | 65 | `rejected` |
| A10 | 2.400 km/h sem anomalia de acesso | `0/0/0/0/25/0` | 25 | `two_factor_required` |
| A11 | Desvio isolado de pelo menos 8 vezes a mediana | `0/15/0/0/0/0` | 25 | `two_factor_required` |
| A12 | Valor 3 vezes a mediana para novo destinatário | `0/10/5/0/0/0` | 25 | `two_factor_required` |
| A13 | PF envia para 12 destinatários em 60 min | `0/0/15/0/0/0` | 25 | `two_factor_required` |
| A14 | Recebedor PF concentra 5 pagadores e 8 tentativas | `0/0/0/0/0/15` | 25 | `two_factor_required` |
| A15 | Mesmo device aparece em 3 pagadores PF | `0/0/0/15/0/0` | 25 | `two_factor_required` |
| A16 | Conta-laranja jovem, inédita e em relação nova | `0/0/5/0/0/10` | 25 | `two_factor_required` |
| A17 | Novo destinatário em valor habitual | `0/0/5/0/0/0` | 5 | `approved` |
| A18 | Conta jovem e inédita abaixo do valor material | `0/0/5/0/0/5` | 10 | `approved` |
| A19 | Device corporativo compartilhado sem outro risco | `0/0/0/15/0/0` | 15 | `approved` |
| A20 | Valor alto sem outro contexto | `10/0/0/0/0/0` | 10 | `approved` |
| A21 | Valor alto em noite incomum | `10/0/0/0/5/0` | 15 | `approved` |
| A22 | Valor muito alto em noite incomum | `20/0/0/0/5/0` | 25 | `two_factor_required` |

A11 a A16 têm soma bruta 15 e usam salvaguardas determinísticas de tipologias
fortes; o score operacional recebe piso 25 para ficar coerente com a decisão.
A10 não rejeita porque localização declarada sem anomalia independente de
acesso ainda pode ser imprecisa. O piso de 2FA nunca habilita rejeição.

## Fronteira de rejeição por burst

A regra usa `contagem >= limite AND volume >= limite`. Abaixo de qualquer uma
das duas fronteiras, a operação recebe no máximo 2FA por exposição agregada.

| Segmento | Tentativas/5 min | Total/5 min | Exposição | Resultado |
|---|---:|---:|---:|---|
| PF | 11 | R$ 50.000,00 | 35 | `two_factor_required` |
| PF | 12 | R$ 49.999,99 | 35 | `two_factor_required` |
| PF | 12 | R$ 50.000,00 | 60 | `rejected` |
| PJ | 19 | R$ 200.000,00 | 25 | `two_factor_required` |
| PJ | 20 | R$ 199.999,99 | 35 | `two_factor_required` |
| PJ | 20 | R$ 200.000,00 | 60 | `rejected` |

Isso é uma rajada de fato: no mínimo uma tentativa a cada 25 segundos para PF
ou a cada 15 segundos para PJ, junto de volume financeiro extremo.

## Fronteiras de fan-out

| Segmento | Abaixo do elevado | Elevado | Crítico + 2FA |
|---|---:|---:|---:|
| PF/indefinido | 0–5 destinos/h | 6–11 | ≥ 12 |
| PJ | 0–14 destinos/h | 15–29 | ≥ 30 |

Fan-out conta destinatários distintos, não quantidade de Pix. Repetir cinco
vezes o mesmo destinatário é tratado por `PAIR_REPETITION_15M`, não por fan-out.

## Perguntas prováveis da banca

### Por que não usar simplesmente o limite Pix do cliente?

O contrato do desafio não fornece limite contratado nem uma fonte bancária
autoritativa. Persistir um número criado pelo antifraude como se fosse limite do
cliente seria conceitualmente errado. Os patamares de R$ 5 mil/R$ 15 mil e
R$ 20 mil/R$ 60 mil são sinais heurísticos segmentados, nunca autorização de
saldo ou limite.

### Por que valor alto vale apenas 10?

Valor sozinho tem alta chance de falso positivo. Dez pontos registram cautela,
mas noite incomum ainda deixa o caso em 15. Para chegar a 2FA, ele precisa de
um contexto mais forte, como device e IP novos (`10 + 18 = 28`), ruptura do
perfil, microrrajada ou acúmulo real.

### Por que valor muito alto vale 20 e não rejeita?

Um Pix grande pode ser legítimo. Vinte pontos deixam a operação perto da
fricção; um segundo sinal fraco, como noite incomum, completa 25. Rejeitar por
valor seria confundir anomalia com fraude comprovada.

### Por que o total de 60 minutos exige contagem mínima?

As janelas incluem a tentativa atual. Sem contagem mínima, um único Pix de
R$ 20 mil seria pontuado como valor atual e novamente como agregado de uma hora.
A R9 exige 3 tentativas PF ou 6 PJ; em 24 horas, 5 PF ou 10 PJ.

### Por que aumentar fan-out?

Quatro destinatários PF ou oito PJ em uma hora ainda podem representar aluguel,
contas, fornecedores ou folha pequena. Os novos limites preservam o sinal, mas
reservam o rótulo crítico para dispersão excepcional. PJ recebe maior tolerância
por natureza operacional.

### Um fraudador pode ficar logo abaixo dos limiares?

Sim; todo motor determinístico tem fronteiras. Por isso as regras se sobrepõem
por dimensão: microrrajada, totais de 60 min/24 h, desvio da mediana e p90,
device/IP, contraparte e rede do recebedor. Ficar abaixo de uma regra não apaga
os outros sinais.

### Esses números são comprovados estatisticamente?

Não. São heurísticas explicáveis e coerentes para um hackathon sem dados reais
rotulados. Em produção, seriam calibradas por segmento usando fraude confirmada,
falso positivo, custo de 2FA e perda evitada.

## Parecer

A R9 reduz fricção onde havia dupla contagem e limites baixos, sem relaxar as
tipologias de tomada de conta, conta-laranja, geovelocidade impossível ou
convergência crítica. Para o hackathon, a política é determinística,
explicável e defensável. Para produção, permanece obrigatória a calibração em
dados rotulados e telemetria autenticada.
