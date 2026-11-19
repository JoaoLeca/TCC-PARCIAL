# Kit da avaliação com participantes

Material para aplicar a avaliação de uso descrita em [`eval/roteiro_automacao.md`](../roteiro_automacao.md). Nada aqui contém resultados; os arquivos são modelos para preencher durante a aplicação.

| Arquivo | Uso |
| --- | --- |
| [`termo_consentimento.md`](termo_consentimento.md) | Modelo de termo de consentimento livre e esclarecido (TCLE). Revisar com a orientadora antes de usar. |
| [`roteiro_tarefas.md`](roteiro_tarefas.md) | Tarefas entregues ao participante, cartões com dados fictícios, ordens de aplicação e protocolo do observador. |
| [`ficha_observacao.csv`](ficha_observacao.csv) | Planilha de registro, uma linha por participante e tarefa. |
| [`../questionario_likert.md`](../questionario_likert.md) | Questionário de satisfação aplicado ao final. |
| [`../cenarios_fcr.csv`](../cenarios_fcr.csv) | Modelo para os dois avaliadores registrarem a resolução no primeiro contato. |

## Antes de aplicar

- [ ] Confirmar com a orientadora se a avaliação precisa passar por comitê de ética (CEP) e ajustar o termo conforme a orientação recebida.
- [ ] Definir com a orientadora a quantidade de participantes e o perfil (por exemplo, clientes de barbearia, colegas ou familiares maiores de 18 anos). Não há meta numérica predefinida.
- [ ] Preparar uma instalação de avaliação conforme a seção “Preparação reproduzível” do roteiro: banco separado, conta de dono, registro do *commit*, da data, do fuso e da configuração (IA ligada ou desligada, Telegram ou não).
- [ ] Imprimir ou preparar em tela os cartões de dados fictícios e as tarefas, na ordem definida para cada participante.
- [ ] Criar o formulário do questionário de satisfação sem campo de nome ou e-mail.
- [ ] Combinar quem observa, quem registra a ficha e quem serão os dois avaliadores de FCR.

## Durante a sessão (cerca de 25 minutos por participante)

1. Apresentar o objetivo em linguagem simples, entregar o termo e só continuar após o consentimento.
2. Atribuir um identificador local (`P01`, `P02`…) e a ordem de tarefas (A, B ou C). Não anotar nome nem telefone real.
3. Abrir o site em uma sessão nova do navegador e entregar o cartão de dados fictícios.
4. Ler cada tarefa sem explicar botões ou como escrever as mensagens. Seguir o protocolo do observador em `roteiro_tarefas.md` e preencher uma linha da ficha por tarefa.
5. Ao final, aplicar o questionário de satisfação e a pergunta aberta.
6. Conferir no painel da equipe ou no banco o efeito das tarefas de reserva, remarcação, cancelamento e retorno da equipe. Uma resposta do assistente não comprova a gravação.

## Depois da aplicação

- **Tarefas:** `uv run python eval/metrics.py --tasks eval/participantes/ficha_preenchida.csv` calcula, por tarefa, a quantidade de participantes, as conclusões independentes, com ajuda e não concluídas e a mediana do tempo.
- **FCR:** copiar `eval/cenarios_fcr.csv` para cada condição avaliada, preencher os campos dos dois avaliadores, reconciliar os campos de consenso e calcular com `--fcr`. A coluna `fcr_ids` da ficha indica quais cenários cada tarefa exercita.
- **Satisfação:** exportar as respostas sem e-mail, renomear a coluna do item analisado para `rating` e calcular com `--likert`. Apresentar também a distribuição de cada item.
- **Telemetria:** exportar com `eval/export_logs.py --database <banco de avaliação>` e calcular com `--latency` e `--operations`.

No TCC, apresentar a quantidade de participantes e de tarefas aplicadas, os denominadores, os dados faltantes e as limitações, sem completar dados ausentes. Os arquivos preenchidos, com tempos e comentários, devem ficar guardados de forma privada; publicar só os resultados agregados.
