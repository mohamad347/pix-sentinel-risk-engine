# Como o projeto é organizado

O PIX SENTINEL segue o fluxo arquitetural do Base Service:

```text
requisição HTTP
  → middlewares
  → resource
  → JSON Schema
  → controller
  → repository
  → models / PostgreSQL
  → DTO
  → resposta HTTP
```

## Responsabilidade de cada camada

| Pasta | Responsabilidade |
|---|---|
| `resources/` | Receber a requisição, validar o contrato de entrada, chamar o controller e definir a resposta HTTP |
| `schemas/` | Declarar os JSON aceitos pela API |
| `controllers/` | Orquestrar a regra de negócio, a política antifraude e o commit da transação |
| `repositories/` | Executar consultas e alterações no banco usando a sessão recebida pelo contexto |
| `models/` | Mapear as tabelas SQLAlchemy |
| `dtos/` | Transformar objetos internos no contrato público da resposta |
| `errors/` | Definir e converter erros no envelope QI Tech |
| `middlewares/` | Aplicar token interno, correlação, logs e ciclo de vida da sessão |
| `connectors/` | Encapsular chamadas HTTP externas quando alguma integração for necessária |
| `utils/` | Manter ferramentas genéricas compartilhadas |

## Fluxo Pix atual

O endereço e o verbo são registrados explicitamente em `src/app.py`.
`PixTransactionResource` valida o POST com `post_pix_transaction.json` e
encaminha o payload ao `PixTransactionController`. O controller aplica a
política ativa, usa `PixTransactionRepository` para persistir a tentativa e o
parecer e confirma a transação com um único commit. O middleware fecha a sessão
e executa rollback quando uma exceção escapa do fluxo.

O GET recebe o ID externo do Pix, consulta a análise persistida pelo repository
e usa `PixAnalysisDTO` para devolver somente `key`, `status` e `metadata`.

## Sessão e transação

O middleware em `src/middlewares/session_manager.py` prepara um `Context` por
requisição. Somente o controller obtém esse contexto por meio de
`BaseController`; o repository recebe o contexto e usa sua sessão. O commit
pertence ao controller, pois ele conhece o limite completo da operação.

Como `SessionLocal` usa `autoflush=False`, o repository chama `flush()` quando
precisa materializar a chave da tentativa antes de criar a análise. Tentativa e
análise permanecem na mesma transação.

## Entrada e saída

`src/schemas/post_pix_transaction.json` cuida da estrutura de entrada. As
validações que dependem da semântica de Python, como inteiro estrito e datetime
com fuso, ficam no controller.

`src/dtos/pix_analysis_dto.py` define a saída pública. Dados sensíveis do
payload e evidências internas não devem ser acrescentados ao DTO.

## Banco de dados

`database/database.sql` é o schema de inicialização de instalações novas. Ele
não é um mecanismo de migração para bancos existentes. Alterações em bases já
criadas exigem SQL incremental revisado para aquele ambiente.
