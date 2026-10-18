# PIX SENTINEL — ROTAS v1 | Contrato HTTP do MVP exclusivamente antifraude

> **Destino sugerido no repositório:** `docs/ROTAS.md`  
> **Estado:** `PROPOSTO_PARA_ACEITE` — especificação de interface, **não** código implementado nem teste executado.  
> **Prioridade:** edital técnico `Desafio Antifraude Pix.pdf` → mudança de escopo `PIX_SENTINEL_DIRETRIZ_MVP_ANTIFRAUDE_PARA_CODEX_v1.md` → `PIX_SENTINEL_ERD_v1.md` → esta proposta de rotas. Decisões antigas sobre core bancário não governam o MVP.  
> **Entrega de apoio:** `PIX_SENTINEL_ROTAS_v1.openapi.yaml` (OpenAPI 3.1 proposta, compatível com este documento).  
> **Próximo artefato:** FLUXOS v1 (transações, concorrência, ordenação, janelas e falhas).  
> **Objetivo:** contrato pequeno, explícito, implementável pelo Codex com o Base Service, sem congelar política/thresholds de risco.

## 1. Problema de negócio e escopo da API

**Problema:** antes de um Pix ser executado, emitir rapidamente um **parecer antifraude explicável** a partir do payload da tentativa, histórico local de tentativas e relógio do servidor. Reduzir risco e fricção sem alegar fraude comprovada ou pagamento liquidado.

**As únicas rotas de negócio do MVP são as duas oficiais:**

| Método | Caminho | Finalidade | HTTP no sucesso (proposta) |
|---|---|---|---|
| `POST` | `/pix/transaction` | Receber uma tentativa distinta, avaliar, persistir e devolver o parecer | `200 OK` (nova análise **e** replay idêntico) |
| `GET` | `/pix/transaction/{id}` | Retornar a análise persistida de um Pix pelo **ID externo** da entrada | `200 OK` |

Não criar rotas de cadastro, saldo, limites bancários, mock/core, pagamento, autenticação, challenge, confirmação de Pix ou listagem. `/docs`, `/openapi.json` e endpoints de infraestrutura que já existam no Base Service não são novas rotas de negócio, e sua disponibilidade deve seguir a configuração do template.

**Decisão ROT-01 — `{id}` significa o campo `id` do Pix recebido no POST**, materializado como `pix_transactions.external_id`. O `key` da resposta é o UUID da **análise**, persistido em `pix_analyses.analysis_key`. Não criar outro GET por `key` nesta versão. Essa interpretação é uma **decisão da equipe para resolver a ambiguidade do edital**, não uma definição explicitada pelo documento oficial.

**HTTP status != decisão antifraude:** uma tentativa válida com decisão `rejected` ainda é análise realizada com sucesso HTTP **200**. Erros HTTP (`409`, `422`, `500` ou `503`) representam problemas de contrato ou infraestrutura, **não** um parecer antifraude.

## 2. `POST /pix/transaction`

### 2.1 Requisição oficial (`Content-Type: application/json`)

```json
{
  "id": "pix-000123",
  "amount": 150000,
  "transaction_date": "2026-10-17T22:15:00-03:00",
  "source_account": {
    "branch": "0001",
    "account_number": "88213",
    "opening_date": "2025-03-02T10:00:00-03:00",
    "owner": { "document_number": "123.456.789-09" }
  },
  "destination_account": {
    "participant": "60701190",
    "branch": "3675",
    "account_number": "10442",
    "opening_date": "2026-10-10T09:00:00-03:00",
    "owner": { "document_number": "987.654.321-00" }
  },
  "source": {
    "device_id": "dvc-9f2c71a8",
    "channel": "app",
    "ip": "198.51.100.23",
    "geolocation": { "latitude": -23.56, "longitude": -46.65 }
  }
}
```

`source.geolocation` é **opcional**: ausência é válida. `destination_account.participant` existe, `source_account.participant` **não faz parte do contrato**. Não exigir `bank_id`, `balance`, `account_status`, `registered_devices`, score externo, CPF verificado nem nenhum outro campo de core. Campo `amount` está em **centavos**, como inteiro JSON (sem float, string ou bool).

### 2.2 DTOs lógicos propostos (nomes são sugestões ao Codex)

| Modelo | Campos | Regra |
|---|---|---|
| `OwnerIn` | `document_number: str` | CPF/CNPJ como **string**, preservando zeros à esquerda; normalização alfanumérica segura internamente |
| `SourceAccountIn` | `branch: str`, `account_number: str`, `opening_date: datetime`, `owner: OwnerIn` | **Sem** `participant` de origem |
| `DestinationAccountIn` | `participant: str`, `branch: str`, `account_number: str`, `opening_date: datetime`, `owner: OwnerIn` | `participant` é o ISPB de destino |
| `GeolocationIn` | `latitude: number`, `longitude: number` | Opcional como **objeto inteiro**; lat `[-90, 90]`, lon `[-180, 180]` |
| `PixSourceIn` | `device_id: str`, `channel: str`, `ip: str`, `geolocation?: GeolocationIn` | Device/IP declarados, não confiáveis/cadastrados |
| `PixTransactionIn` | `id: str`, `amount: int`, `transaction_date: datetime`, `source_account`, `destination_account`, `source` | Todos obrigatórios exceto `source.geolocation` |

Regras de validação **propostas** (não impostas numericamente pelo edital):

1. `id` deve ser string não vazia, sem barras `/` nem espaços nas extremidades, pois é usado como segmento da rota GET. **Não converter `id` em UUID**: o exemplo oficial é `pix-000123`. Eventual tamanho máximo técnico deve ser compatibilizado com o Base Service; não rejeitar IDs válidos por limites arbitrários não documentados.
2. `amount` deve ser **inteiro estrito** positivo (`> 0`, cabendo em `BIGINT`), em centavos. Rejeitar `1500.0`, `"1500"`, `true`, zero e negativos. Não verificar suficiência de saldo.
3. `transaction_date` e os dois `opening_date` devem ser ISO 8601 **com fuso** (`Z` ou offset); entradas sem fuso retornam `422`. Preservar data declarada e normalizar instantes para operações temporais. Datas atípicas não são automaticamente fraude comprovada; política de qualidade temporal será definida em FLUXOS.
4. Agência, conta, participante e documentos são **strings**, com zeros à esquerda preservados; documentos podem conter CNPJ alfanumérico. Não lançar os identificadores para `int` nem retirar letras de um CNPJ válido.
5. Geo ausente não gera erro e não é evidência adversa por si só. Se presente, exigir ambas as coordenadas finitas nos intervalos geográficos; `null` é aceito como ausência **nesta proposta v1**; DTO e canonizador devem tratá-lo como equivalente ao campo omitido.
6. Campos extras fora do contrato oficial **não podem ser exigidos nem afetar risco ou fingerprint**. Para compatibilidade com payloads ampliados, a proposta é ignorar campos extras no DTO (`extra=ignore`), mantendo em `request_payload` o **objeto oficial validado**, não extras não reconhecidos. Ajustar apenas se padrão existente do Base Service exigir outra convenção e registrar a mudança.
7. Não validar CPF/CNPJ com regras de dígito que rejeitem massa fictícia do edital sem decisão específica. Uma normalização interna de agrupamento pode remover apenas pontuação permitida e converter letras de CNPJ para maiúsculas, mantendo zeros.

**Nota de implementação:** `bool` não pode ser aceito como `int` para `amount`; usar tipo estrito na camada de DTO, e evitar coerção permissiva de Pydantic. Os timestamps precisam ser realmente timezone-aware (não apenas strings bem formatadas).

### 2.3 Resposta de sucesso: `200 OK`

O edital exige **exatamente os campos principais** `key`, `status` e `metadata`, com `status` em `approved`, `two_factor_required` ou `rejected`:

```json
{
  "key": "b3f1c2e0-7d4a-4c1e-9a52-1f0e6c9d2a77",
  "status": "two_factor_required",
  "metadata": {
    "schema_version": "1.0",
    "policy_version": "dev-example",
    "reason_code": "ADDITIONAL_VERIFICATION",
    "reason_description": "Verificacao adicional recomendada pela politica de risco."
  }
}
```

O bloco de `metadata` acima é **padrão próprio proposto**, pois o edital deixa o conteúdo livre. `dev-example` é ilustrativo: usar uma **versão real da configuração experimental em uso**, sem chamar parâmetros de laboratório de política aprovada. Motivos candidatos (não um enum congelado):

| Decisão | `reason_code` de exemplo | Interpretação |
|---|---|---|
| `approved` | `RISK_ACCEPTED` | Parecer antifraude favorável, **não** Pix liquidado |
| `two_factor_required` | `ADDITIONAL_VERIFICATION` | Necessidade recomendada de verificação; **não** 2FA executado |
| `rejected` | `CRITICAL_FRAUD_RISK` | Rejeição por política antifraude, **não** fraude comprovada |

`metadata` sempre será objeto JSON com **motivo explicável e versionamento**. Não incluir no MVP `banking_eligibility`, `INSUFFICIENT_FUNDS`, `ACCOUNT_BLOCKED`, saldo, desafios `push_confirmation`/`facial_liveness` ou promessas de autenticação real. Não divulgar score detalhado, thresholds, documentos, números de contas, IP, geolocalização precisa nem `evidence_snapshot` interno.

### 2.4 Idempotência e conflitos (ROT-02)

| Situação | HTTP | Comportamento |
|---|---|---|
| Pix novo e válido | `200` | Analisar, persistir tentativa + análise e retornar snapshot |
| Mesmo `id` + **mesmo payload canônico** | `200` | Retornar os **mesmos** `key`, `status` e `metadata`, sem gravar outra tentativa, mudar `received_at` ou recomputar score |
| Mesmo `id` + payload canônico diferente | `409 Conflict` | Não substituir dados nem análise já gravados |
| Mesmo `id` simultâneo em duas requisições | `200` para ambas se mesmo payload (após resolução da corrida) | Uma só tentativa/análise; depender de UNIQUE + transação/estratégia em FLUXOS |
| Repetição após mudança na política | `200` | Retornar resultado original, ainda que a política atual fosse decidir diferente |
| Repetição após erro transacional com rollback | Processar como novo quando nenhum registro foi confirmado | Não retornar resultado supostamente salvo |

**Fingerprint:** SHA-256 de uma representação canônica dos **campos oficiais validados**, com nomes/ordem estáveis, ordenação determinística das chaves, tratamento equivalente para geo ausente versus nula, identificadores normalizados e timestamps convertidos para instante equivalente. Excluir `received_at`, score, `analysis_key` e qualquer campo gerado pelo servidor. Guardar algoritmo/canonizador sob testes; mudar sua versão requer política de compatibilidade com registros existentes. `request_payload` guarda a visão validada da tentativa, não o hash isolado. SHA-256 aqui é mecanismo de igualdade/replay, **não** autenticação ou proteção de PII.

**Cuidado de corrida:** consultar existência antes de inserir **não basta**; usar `UNIQUE(external_id)` e tratar disputa dentro de transação, conforme FLUXOS. Não incluir duas vezes o Pix atual nas janelas de histórico. Serialização de **Pix diferentes do mesmo pagador** continua uma decisão do documento FLUXOS, não uma garantia implícita desta rota.

## 3. `GET /pix/transaction/{id}`

**Parâmetro `id`:** string com o **ID externo do Pix** (`POST.body.id`) — por exemplo `pix-000123`; **não** o UUID `key` da análise. Consultar `pix_transactions.external_id`, associar via `pix_analyses.transaction_id` e devolver o snapshot original público.

### Sucesso: `200 OK`

```json
{
  "key": "b3f1c2e0-7d4a-4c1e-9a52-1f0e6c9d2a77",
  "status": "two_factor_required",
  "metadata": {
    "schema_version": "1.0",
    "policy_version": "dev-example",
    "reason_code": "ADDITIONAL_VERIFICATION",
    "reason_description": "Verificacao adicional recomendada pela politica de risco."
  }
}
```

O GET deve retornar a **mesma estrutura, mesmos dados e mesma decisão** do POST original. Nunca recalcular score, consultar histórico novamente, gerar outro UUID nem divulgar `request_payload`, `request_fingerprint` ou `evidence_snapshot`. Ausência de registro: `404 Not Found`.

## 4. Política HTTP de erros (ROT-03)

Códigos aqui são **decisões da equipe propostas para o MVP**, não requisitos detalhados do edital; confirmar compatibilidade com o tratador de exceções do Base Service antes de implementar.

| HTTP | Onde | Causa | Contrato de erro seguro |
|---|---|---|---|
| `200` | POST/GET | Parecer emitido/recuperado, inclusive `rejected` | `key/status/metadata` |
| `404` | GET | Pix `id` não encontrado | `{"detail":"PIX_ANALYSIS_NOT_FOUND"}` |
| `409` | POST | Mesmo `id`, payload divergente | `{"detail":"PIX_ID_CONFLICT"}` |
| `422` | POST | Falha estrutural/tipo/valor de campo obrigatório ou fuso ausente | Erro de validação sanitizado; formato poderá seguir o Base Service |
| `415` | POST | Corpo com media type não suportado | `{"detail":"UNSUPPORTED_MEDIA_TYPE"}` (se middleware do Base Service aplicar) |
| `500` | POST/GET | Falha inesperada | `{"detail":"INTERNAL_ERROR"}` sem stack trace/PII |
| `503` | POST/GET | Banco indisponível/erro reconhecido de dependência | `{"detail":"SERVICE_UNAVAILABLE"}`; não emitir `rejected` fictício |

Para GET com `id` vazio/inválido, usar a convenção do framework sem inventar uma resposta que viole o roteamento. `application/json` é o formato dos retornos. HTTP `422` não pode significar alto risco; HTTP `409` não pode significar fraude. Não retornar `200` antes do commit. Formatos de erros podem ser adaptados ao padrão do Base Service **sem modificar o contrato de sucesso oficial**.

## 5. Persistência e mapeamento ao ERD v1

| Informação | Campo ERD | Origem/uso |
|---|---|---|
| Identificador Pix | `pix_transactions.external_id` | `POST.id`; procura GET/replay |
| Dados oficiais validados | `pix_transactions.request_payload` | Snapshot interno, contém dados sensíveis |
| Assinatura de replay | `pix_transactions.request_fingerprint` | Comparar payloads canônicos |
| Valor solicitado | `pix_transactions.amount_cents` | `amount` exato em centavos |
| Momento declarado | `pix_transactions.transaction_at` | `transaction_date` com fuso |
| Momento recebido | `pix_transactions.received_at` | Servidor; usado em velocity, gerado uma vez |
| Dimensões históricas | `payer_*`, `payee_*`, `source_device_id` | Consultas locais, identidade limitada pelo payload |
| Chave da resposta | `pix_analyses.analysis_key` | `key` UUID persistido |
| Decisão oficial | `pix_analyses.status` | Enum dos três estados |
| Motivo público | `pix_analyses.metadata` | `metadata` objeto salvo, imutável no GET |
| Evidências internas | `pix_analyses.evidence_snapshot` | Sem exposição pública |
| Versão da política | `pix_analyses.policy_version` | A mesma registrada no `metadata` |

**Atomicidade:** inserção da transação Pix e da análise deve ocorrer numa mesma transação de BD. Em caso de falha, nenhum registro parcial deve ser considerado sucesso. **Limite das garantias:** o ERD e as rotas não resolvem sozinhos a concorrência entre Pix distintos; FLUXOS v1 deve definir locks/contabilização para velocity e montante.

## 6. TDD mínimo — checklist de rotas para Codex

| ID | Teste (especificação; **não executado**) | Oráculo |
|---|---|---|
| `MVP-T01` | POST oficial válido | HTTP 200; `key` UUID; status permitido; `metadata` objeto; par Pix/análise persistido |
| `MVP-T02` | GET pelo mesmo ID Pix | HTTP 200; JSON público idêntico ao POST original |
| `MVP-T03` | POST idêntico repetido | Mesma resposta; 1 Pix e 1 análise; nenhum novo `received_at` |
| `MVP-T04` | Mesmo ID com valor ou outro campo alterado | HTTP 409; snapshot antigo inalterado |
| `MVP-T05` | Geo ausente; zeros à esquerda; documento alfanumérico | HTTP 200 e sem corrupção dos identificadores |
| `MVP-T06` | Amount float/string/bool/zero/negativo ou timestamp sem fuso | HTTP 422; nenhum registro persistido |
| `MVP-T07` | Primeiro Pix observado | Sem tratar falta de histórico como fraude confirmada (oráculo da política experimental) |
| `MVP-T08` | Velocity com janela e reenvio | Pix distintos contados uma vez; replay não altera agregados |
| `MVP-T09` | Cenários duvidoso e crítico com configuração experimental explícita | `two_factor_required` e `rejected` com motivos coerentes, sem saldo/core |
| `MVP-T10` | Falha de persistência simulada | Sem HTTP 200 ou gravação parcial |

**Prioridade de trabalho:** primeira fatia POST + persistência + GET (`T01`, `T02`, `T05`, `T06`); depois replay/conflito (`T03`, `T04`); depois histórico/regras (`T07`–`T09`); falhas transacionais (`T10`) antes de declarar pronto. Cenários adicionais relevantes: 404, concorrência idêntica/divergente, metadata sem PII, logs seguros, carga/latência. Não alegar que catálogo antigo (336) ou laboratório (47) passou contra a API real.

## 7. Pendências deliberadamente encaminhadas a FLUXOS v1

- Ordem da validação, lookup, lock, insert, agregação de histórico, avaliação, commit e resposta.
- Se o Pix atual entra nas agregações SQL após insert ou é somado explicitamente uma vez (escolher apenas um mecanismo).
- Fronteiras inclusivas/exclusivas de janela `[início, fim]`; zoneamento, `received_at`, eventos simultâneos e concorrência por pagador.
- Tratamento seguro de `UNIQUE` em disputas, rollback e consistência transacional por pagador com diferentes `external_id`.
- Contingência do banco indisponível; metas de latência **medidas**, não inventadas.
- Versão do fingerprint, eventuais evoluções do DTO e política de rollback/migration.
- Política antifraude experimental, motivo e thresholds numéricos **ainda não congelados**.

## 8. Instrução operacional ao Codex

1. Leia o **edital oficial**, a diretriz MVP antifraude (que remove core/mock), `ERD.md` e este `ROTAS.md`.
2. Inspecione o Base Service real (stack, routers, padrões de erro, Pydantic/ORM, banco e migrations). **Não duplique abstrações já existentes.**
3. Após a equipe entregar FLUXOS v1, propor fatias verticais pequenas: DTO + POST/GET + transação → replay/409 → consultas históricas → poucas regras configuráveis + testes.
4. Não gerar endpoints de execução financeira, mock bancário, liveness/push, nem incluir `banking_eligibility` ou `INSUFFICIENT_FUNDS`.
5. Manter o contrato de sucesso oficial. Registrar qualquer ajuste de HTTP/DTO necessário à compatibilidade com o Base Service **antes de alterar esta especificação**.
6. Documentar testes realmente executados e limitações; não trocar rótulos sintéticos da Etapa 4 por validação da API.

---

### Auditoria documental esperada

- [ ] Apenas dois endpoints públicos de negócio (`POST` e `GET`).
- [ ] `GET/{id}` = `external_id` do Pix; `key` = UUID da análise.
- [ ] Entrada oficial completa, geo opcional, centavos inteiros e timestamps com fuso.
- [ ] Saída com `key`, `status` nos três literais e `metadata` objeto.
- [ ] `200/404/409/422/500/503` sem atribuição de erros a status antifraude.
- [ ] Idempotência baseada em `external_id` + fingerprint; replay preserva snapshot.
- [ ] Nenhuma consulta a core/saldo nem decisão por elegibilidade financeira.
- [ ] Correspondência com ambas as tabelas do ERD v1.
- [ ] Fluxos/concorrência não declarados como implementados.
- [ ] Parâmetros de risco ainda não congelados.

**Referências:** `Desafio Antifraude Pix.pdf`, páginas 3–5 (JSON e endpoints) e 6–7 (Base Service/README/avaliação); `PIX_SENTINEL_DIRETRIZ_MVP_ANTIFRAUDE_PARA_CODEX_v1.md`; `PIX_SENTINEL_ERD_v1.md`.
