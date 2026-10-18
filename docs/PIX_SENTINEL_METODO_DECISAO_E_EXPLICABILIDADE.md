# Método de decisão e explicabilidade do PIX SENTINEL

**Política vigente:** `mvp-risk-adaptive-v3-r4`  
**Escopo:** motor antifraude Pix do hackathon  
**Natureza do score:** ordinal e determinístico; não é probabilidade de fraude

## 1. Problema que o método resolve

Uma tentativa Pix precisa ser decidida em poucos segundos. Um único sinal pode
ser legítimo: valor alto pode ser aluguel, IP novo pode ser troca de rede e
geolocalização distante pode ser erro do dispositivo. Por outro lado, ignorar a
combinação desses sinais pode liberar uma tomada de conta.

O método precisa equilibrar dois custos:

- falso negativo: aprovar uma tentativa arriscada;
- falso positivo: bloquear um cliente legítimo.

Por isso o PIX SENTINEL separa três resultados:

- `approved`: risco observado abaixo do limiar de fricção;
- `two_factor_required`: existe dúvida suficiente para confirmar identidade;
- `rejected`: uma salvaguarda crítica foi satisfeita.

Esses resultados são pareceres do motor. Eles não liquidam o Pix, não executam
o 2FA e não comprovam fraude.

## 2. Método escolhido

Foi escolhido um **score contextual por famílias independentes, com tetos e
salvaguardas explícitas de rejeição**.

O fluxo é:

```text
payload atual
    + histórico local observado
    ↓
29 sinais determinísticos
    ↓
6 famílias de risco com teto
    ↓
score ordinal de 0 a 100
    ↓
salvaguardas críticas
    ↓
approved | two_factor_required | rejected
```

### Por que este método

1. **É auditável.** Dado o mesmo payload, histórico e versão da política, a
   decisão é reproduzível.
2. **Evita dupla contagem.** Sinais que descrevem o mesmo fenômeno competem
   dentro de uma família em vez de serem somados livremente.
3. **Distingue dúvida de certeza operacional.** Uma anomalia material pede 2FA;
   rejeição exige uma condição crítica ou convergência independente.
4. **Funciona sem fonte externa.** Usa apenas o contrato oficial e o histórico
   armazenado pelo próprio motor.
5. **É compatível com os dados disponíveis.** Não finge estimar uma
   probabilidade sem amostra bancária rotulada.

## 3. Alternativas avaliadas

| Alternativa | Vantagem | Motivo para não ser o método vigente |
|---|---|---|
| Regras binárias independentes | Muito simples | Não representa risco intermediário e favorece dupla contagem |
| Soma irrestrita de todas as regras | Fácil de implementar | Vários sinais correlacionados poderiam produzir rejeição artificial |
| Rejeição por score puro | Ordem simples | Um único grupo de sinais poderia bloquear sozinho |
| Machine learning supervisionado | Poderia aprender interações | Não há dados rotulados independentes para treinar e validar |
| Aprovação por contraparte conhecida | Reduz fricção | Histórico local não comprova que a contraparte continue segura |

O modelo escolhido preserva a clareza das regras sem tratar o score como
estatística calibrada.

## 4. Evidências utilizadas

O motor usa janelas deslizantes de 1, 5, 15, 60 e 1.440 minutos. O instante de
admissão vem do relógio do PostgreSQL e a tentativa atual entra uma única vez em
cada janela.

Nos níveis sobrepostos, somente o sinal mais forte da dimensão é registrado.
Por exemplo, quatro tentativas PF em um minuto geram `MICROBURST_1M` (25), não
uma segunda ocorrência redundante da frequência de cinco minutos (15).

As evidências incluem:

- valor atual, contagem e montante acumulado;
- mediana e percentil 90 do perfil aprovado;
- novidade, repetição, fan-in e fan-out de contraparte;
- dispositivo, IP e canal;
- horário, divergência de relógio e velocidade geográfica;
- tipo PF/PJ inferido pelo formato do documento;
- idade declarada das contas de origem e destino;
- igualdade de titular entre origem e destino.

Perfil e referência geográfica usam somente análises anteriores `approved`.
Velocity continua considerando todas as tentativas válidas, porque uma
tentativa negada ainda é evidência de atividade.

O período noturno é 20h–06h em `America/Sao_Paulo`. Como o contrato não informa
o horário personalizado nem o limite bancário do cliente, essa evidência não
aplica limite regulatório: vale apenas 5 pontos e exige que o período seja raro
no histórico aprovado do pagador.

### Segmentação PF/PJ

A segmentação não presume que PJ seja mais segura. Ela reconhece que valores,
dispersão para fornecedores ou folha e concentração em um comércio têm escalas
diferentes das operações pessoais. Por isso apenas exposição, fan-out, fan-in,
conta jovem e cold start possuem limiares segmentados. Perfil relativo, acesso e
geolocalização mantêm a mesma lógica.

| Parâmetro | PF ou indefinido | PJ |
|---|---:|---:|
| Valor atual elevado | R$ 5.000 | R$ 15.000 |
| Frequência elevada em 5 min | 4 tentativas | 8 tentativas |
| Microrrajada em 1 min | 4 tentativas | 6 tentativas |
| Total elevado em 60 min | R$ 15.000 | R$ 50.000 |
| Total elevado em 24 h | R$ 50.000 | R$ 150.000 |
| Volume crítico em 5 min, com 8 tentativas | R$ 30.000 | R$ 100.000 |
| Fan-out elevado/crítico | 4 / 8 | 8 / 15 |
| Fan-in do recebedor: pagadores/tentativas | 5 / 8 | 15 / 25 |
| Conta de destino jovem: valor material | R$ 3.000 | R$ 10.000 |
| Cold start material: valor | R$ 3.000 | R$ 10.000 |

O tipo `unknown` usa os limiares de PF, evitando que um documento não
classificável receba automaticamente os limites mais permissivos de PJ.

### Cold start determinístico

Ausência de histórico continua neutra. A exceção explícita é a combinação:

```text
primeira tentativa observada pelo motor
+ conta de origem declarada com menos de 30 dias
+ valor material para o segmento PF/PJ
= 2FA obrigatório, nunca rejeição automática
```

Isso não prova que seja a primeira conta do cliente no banco. A regra significa
somente “primeira observação local de uma conta de origem recentemente aberta”.

## 5. Como os pontos são combinados

Cada sinal produz um valor dentro de uma família. A combinação depende da
natureza dos sinais:

| Família | Combinação | Teto | Racional |
|---|---|---:|---|
| Exposição/velocity | maior nível por dimensão | 60 | níveis sobrepostos não são repetidos: 5 min vale 15, microrrajada 25, estruturação 35 e rajada crítica 60 |
| Perfil | maior desvio ou cold start aplicável | 15 | p90 e múltiplo da mediana medem ruptura; conta jovem cobre ausência inicial de perfil |
| Relacionamento | maior nível aplicável | 15 | novidade, repetição e fan-out são gradações do relacionamento |
| Acesso | soma e pisos de severidade | 20 | device, IP e canal oferecem evidências complementares |
| Temporal | maior nível aplicável | 25 | relógio, horário e deslocamento podem ser correlacionados |
| Rede do recebedor | composição limitada | 20 | fan-in, primeira observação e conta jovem podem coexistir |

Formalmente:

```text
score = min(
    100,
    exposição
    + perfil
    + relacionamento
    + acesso
    + temporal
    + rede_do_recebedor
)
```

O score é uma ordem de severidade. Um score 50 significa mais sinais ou sinais
mais fortes que um score 20; não significa 50% de chance de fraude.

## 6. Exemplo completo

Considere um Pix PF de R$ 6.000 que seja a quarta tentativa em cinco minutos,
para um destinatário novo, usando dispositivo e IP novos.

```text
Família exposição
  ELEVATED_CURRENT_VALUE .......... 25
  ELEVATED_ATTEMPT_COUNT_5M ....... 15
  combinação por máximo ........... 25

Família acesso
  NEW_DEVICE ...................... 10
  NEW_IP ..........................  8
  soma abaixo do teto ............. 18

Família relacionamento
  NEW_COUNTERPARTY ................  5

Score total = 25 + 18 + 5 = 48
```

Os dois sinais de exposição não geram 40 pontos porque ambos descrevem
concentração transacional. Acesso e relacionamento entram separadamente porque
são contextos diferentes.

Como o score é 48 e nenhuma salvaguarda de rejeição foi satisfeita:

```text
status = two_factor_required
challenge = push_confirmation
```

## 7. Como o score vira decisão

As condições são avaliadas em ordem:

| Prioridade | Condição | Resultado |
|---:|---|---|
| 1 | pelo menos 8 tentativas e volume crítico do segmento em 5 minutos | `rejected` |
| 2 | velocidade geográfica impossível e acesso >= 10 | `rejected` |
| 3 | score >= 60, pelo menos 4 famílias e acesso >= 18 ou rede >= 15 | `rejected` |
| 4 | cold start material em conta de origem com menos de 30 dias | `two_factor_required` |
| 5 | acesso >= 18 e risco de relacionamento ou da rede do recebedor | `two_factor_required` |
| 6 | score >= 25 sem salvaguarda crítica | `two_factor_required` |
| 7 | score < 25 sem salvaguarda crítica | `approved` |

Essa ordem é intencional:

- a rajada crítica é uma condição objetiva de alta exposição imediata;
- geolocalização nunca rejeita sozinha, pois é opcional e declarada;
- score alto não rejeita sozinho: exige quatro famílias e um contexto forte;
- device e IP novos junto de destino arriscado pedem confirmação mesmo com score 23;
- dúvida sem convergência crítica é encaminhada para 2FA.

Assim, uma tomada de conta com acesso anômalo e viagem impossível pode ser
rejeitada com score 57, enquanto um score 60 formado por apenas três famílias
continua em 2FA.

## 8. Escolha do segundo fator

Quando a decisão é `two_factor_required`, o motor recomenda:

| Desafio | Condição |
|---|---|
| `push_confirmation` | fricção padrão sem combinação forte de tomada de conta |
| `facial_liveness` | somente PF: acesso >= 18 junto de perfil >= 10, relacionamento >= 12 ou temporal >= 15 |

O campo tem status `recommended`. O PIX SENTINEL não possui provedor de push,
biometria ou callback de autenticação.

PJ e tipo indefinido permanecem em `push_confirmation`, porque o payload não
identifica nem comprova a pessoa operadora autorizada a representar a empresa.

## 9. Explicabilidade e privacidade

A explicabilidade possui duas camadas.

### Resposta pública

O contrato mantém apenas `key`, `status` e `metadata`. O metadata informa:

- versão da política;
- código e descrição do motivo;
- desafio recomendado quando houver 2FA.

### Evidência persistida

O snapshot interno registra:

- score total;
- pontos por família e respectivos tetos;
- sinais acionados;
- salvaguardas críticas;
- versão do schema e da política.

Documentos, contas, IPs, device IDs e coordenadas não são copiados para essa
explicação derivada. O payload original continua armazenado porque faz parte do
histórico recebido, mas não é devolvido pelo endpoint de análise.

Essa separação mantém a resposta estável e evita expor identificadores. Como
evolução de contrato, uma seleção segura de `risk_score`, `family_scores` e
`triggered_signals` pode ser publicada dentro de `metadata` sem alterar os
três campos oficiais.

## 10. Como os parâmetros foram escolhidos

Os parâmetros são heurísticos e foram submetidos a cenários sintéticos de
comportamento e testes de fronteira. A calibração segue estes princípios:

- uma anomalia material isolada pode alcançar 25 e pedir confirmação;
- sinais fracos precisam convergir para alcançar 25;
- sinais correlacionados não devem se multiplicar;
- valor ou geolocalização isolados não devem rejeitar;
- rejeição deve depender de exposição crítica ou evidências independentes;
- ausência de histórico deve ser neutra; somente a convergência determinística
  de primeira observação, conta de origem jovem e valor material recomenda 2FA.

Os cenários sintéticos caracterizam o comportamento esperado, mas não
constituem amostra independente nem medem precisão produtiva. A segmentação PJ
reduz a fricção conhecida em lotes empresariais, mas seus números continuam
heurísticos até existir base rotulada para calibração.

## 11. Limitações assumidas

- Os parâmetros não foram treinados em fraude bancária rotulada.
- Device, IP e geolocalização são declarados pelo payload.
- Histórico observado não equivale a reputação externa.
- PF/PJ é inferido pelo formato, sem validação cadastral externa do documento.
- Os limiares segmentados não foram calibrados em fraude bancária rotulada.
- “Primeira observação” é local ao motor, não o primeiro Pix da vida do cliente.
- Uma tentativa rejeitada não é fraude confirmada.
- Um parecer aprovado não autoriza saldo, limite ou liquidação.

Essas limitações são apresentadas explicitamente para não transformar uma
heurística de hackathon em alegação de eficácia produtiva.

## 12. Frase de defesa

> O PIX SENTINEL não rejeita porque muitos pontos apareceram por acaso. Ele
> controla correlação dentro das famílias, usa 2FA para a zona de dúvida e só
> rejeita quando existe uma salvaguarda crítica explicável.
