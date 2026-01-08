以下は、添付プロジェクト（TanStack AI フロントエンド＋PydanticAI バックエンドで「SQL生成→承認→実行」「CSVエクスポート（クライアント実行）」を行うデモ）と同等の体験を、**TanStack AI ベースのUI**は維持しつつ、バックエンドを **google/adk-python（Google Agent Development Kit; ADK）** に置き換えて実装するための**詳細設計書**と、**PydanticAI との機能対応表**です。

---

# 1. 目的とスコープ

## 1.1 目的

* TanStack AI のストリーミングUI（SSE）でチャット体験を提供しつつ、バックエンドのエージェント実行基盤を **ADK（google/adk-python）** に統一する。
* 既存デモ同様、以下を実現する：

  1. 自然言語→SQL生成（読み取り専用）
  2. **SQL実行は承認（Human-in-the-Loop）後に実施**
  3. SQL結果を表形式でプレビュー（Artifactとして保持し、UIから取得）
  4. **CSVエクスポートはクライアント（ブラウザ）側ツールとして実行**し、実行結果をバックエンドへ返して会話を継続

## 1.2 非スコープ

* 認証・課金・SLA・マルチテナント本番運用の完全実装（ただし設計上の拡張ポイントは記載）
* RAG（検索）や多エージェント構成の高度化（必要な場合の拡張案は付記）

---

# 2. 前提（採用技術）

## 2.1 フロントエンド

* TanStack AI（SSEストリーミング）
* 既存と同じ StreamChunk ベースの制御（例：`tool-input-available`）を前提
  `tool-input-available` は `toolCallId`, `toolName`, `input` を持つ。

## 2.2 バックエンド

* Python / FastAPI（HTTP + SSE）
* google-adk（ADK）

  * LLM Agent（`LlmAgent`）にツールを付与して動作
  * Python関数をツールとして渡すと、シグネチャやdocstring等を解析してスキーマ化し、LLM が tool を呼べるようにする ([Google GitHub][1])
* ADK の “停止→再開（Resume）” を活用して、承認待ち／外部（クライアント）実行待ちのワークフローを継続する

  * Resumability は `ResumabilityConfig(is_resumable=True)` を App に設定して有効化
  * Resume では `invocation_id` を用いて同一ワークフローを再開（ツールは at-least-once になり得るため冪等性が重要）
* 承認（確認）については、ADK の Tool Confirmation 機能（実験的）を利用可能

  * `FunctionTool(..., require_confirmation=True)` 等で実現でき、ツール実行を一時停止して確認を要求できる
  * REST/Runner 経由で `FunctionResponse` を返して再開可能（`id` は確認要求イベントの `function_call_id` と一致が必要）

---

# 3. 全体アーキテクチャ

## 3.1 コンポーネント構成

* **Frontend（TanStack AI UI）**

  * Chat UI
  * 承認UI（approve/deny）
  * Client Tool 実行（CSV生成・ダウンロード等）
  * Artifact Preview（結果表の表示）

* **Backend（FastAPI + ADK）**

  * `/api/chat`（SSE）: ADK Runner のイベントを TanStack StreamChunk に変換してストリーム送出
  * `/api/continuation`（HTTP）: 承認結果／クライアントツール結果を受け取り、ADK を Resume
  * `/api/data`（HTTP）: Artifact（SQL結果等）の取得
  * **TanStack↔ADK 変換アダプタ層**（重要）

    * ADK Event（function_call / function_response / text）→ TanStack StreamChunk

* **Stores**

  * RunStore（run_id 単位の状態、保留中 toolCall の対応表）
  * ArtifactStore（DataFrame/JSON/CSV などの結果保存）

## 3.2 論理アーキテクチャ図（概念）

```mermaid
flowchart LR
  UI[TanStack AI UI] -->|POST /api/chat (SSE)| API[FastAPI]
  API --> ADK[ADK Runner + App + LlmAgent]
  ADK -->|Event Stream| API
  API -->|StreamChunk SSE| UI

  UI -->|POST /api/continuation| API
  API -->|FunctionResponse + invocation_id で Resume| ADK

  API --> Store[(RunStore/ArtifactStore)]
  UI -->|GET /api/data?id=...| API --> Store
```

---

# 4. データモデル設計

## 4.1 主要ID

* `run_id`

  * フロントエンドが保持する会話スレッド識別子（既存デモ同様）
  * バックエンドでは **ADK session_id** に対応付け（1:1）
* `invocation_id`（ADK）

  * 1回のユーザー入力から最終応答までの実行単位（承認待ち／外部結果待ちで中断され得る）
  * Resume には同一 `invocation_id` が必要
* `tool_call_id`（TanStack / ADK function_call.id）

  * UI 側の承認紐づけに必須（UIは tool-call part の `id` と approval の `toolCallId` を同一視する設計）

## 4.2 RunStore（永続化推奨）

最低限、以下を永続化（Redis / Postgres / SQLite いずれでも可）：

* `run_id: str`
* `adk_session_id: str`（= run_id にして良い）
* `pending: dict[tool_call_id, PendingAction]`

  * `PendingAction.kind`: `"approval"` | `"client_tool"`
  * `PendingAction.invocation_id`: str
  * `PendingAction.adk_confirmation_call_id`: str | None

    * ADK の確認要求（`adk_request_confirmation`）に応答するために必要（※後述）
  * `PendingAction.tool_name`: str
  * `PendingAction.tool_input`: dict（UI表示用の正規化済み引数）
  * `created_at`, `expires_at`

## 4.3 ArtifactStore

* `artifact_id: str`
* `run_id: str`
* `type: "table" | "csv" | "json" ...`
* `payload`: JSON（表なら rows/columns、あるいは Arrow/Parquet でも可）
* `created_at`, `ttl`

---

# 5. 機能設計（ユースケース別シーケンス）

## 5.1 通常チャット（ツール不使用）

1. UI が `/api/chat` にメッセージを送信（SSE開始）
2. Backend は ADK `Runner.run_async(...)` を実行し、Event を逐次取得
3. Event を StreamChunk（text delta）に変換して返す
4. 完了時 `done` チャンク送出

ADK は runtime の Event Loop を中心にイベントを生成し、それをUIへ流す構造

---

## 5.2 SQL 実行（承認→実行）

### 方針

* `execute_sql` ツールは **ADK の confirmation/Resume パターン**で実装する。
* UI には **TanStack の `approval-requested`** を投げ、ユーザーの approve/deny を `/api/continuation` で受ける。
* ADK 側へは `FunctionResponse` を返して再開する（確認応答の `id` 等の要件あり）

### シーケンス（概略）

1. LLM が `execute_sql(sql=...)` を function_call
2. Backend は tool-call chunk を送る（toolCallId=ADK function_call.id）
3. `execute_sql` の実装は **確認未実施なら request_confirmation を要求して中断**（または require_confirmation）
4. ADK から確認要求イベント（`adk_request_confirmation`）が出る
5. Backend はそれを受けて UI に `approval-requested` chunk を送る
6. UI が approve/deny を `/api/continuation` に送る
7. Backend は ADK Runner を **invocation_id 指定で Resume**し、`FunctionResponse(name="adk_request_confirmation", id=...)` を投入
8. 承認なら `execute_sql` を実行して結果を ArtifactStore に保存、tool-result chunk を送る
9. 最終的な自然言語回答（要約）を text chunk で送る

---

## 5.3 CSV エクスポート（クライアントツール実行→結果返却）

### 方針

* `export_csv` は **クライアントツール**として扱うため、バックエンドは「実行要求」を `tool-input-available` としてUIへ通知する。
* ADK 側は **Long Running Function**（または外部結果待ちの中断）として扱い、クライアントが結果を返したら Resume する。
* ADK の Long Running Function は、イベントで long-running function call を検出し、後続で FunctionResponse を返して進行更新できる（Python例でも `runner.run_async` を再度呼び、`function_response` を new_message に入れて継続している） ([Google GitHub][1])

### シーケンス（概略）

1. LLM が `export_csv(artifact_id=..., filename=...)` を function_call
2. Backend は tool-call chunk を送る（toolCallId=ADK function_call.id）
3. Backend は UI に `tool-input-available` chunk を送る（toolCallId を同じにする）
4. UI はブラウザで CSV を生成・ダウンロード等を行い、結果（成功/失敗・メッセージ）を `/api/continuation` に POST
5. Backend は ADK Runner を Resume し、`FunctionResponse(id=toolCallId, name="export_csv", response={...})` を投入して継続 ([Google GitHub][1])
6. ADK がその結果を踏まえて最終応答（完了メッセージ）を生成 → text chunk

---

# 6. バックエンド詳細設計

## 6.1 ディレクトリ/モジュール設計（推奨）

* `backend/`

  * `app.py`：ADK App 構築（root_agent, tools, resumability）
  * `server.py`：FastAPI ルーティング（/api/chat, /api/continuation, /api/data）
  * `adapters/`

    * `tanstack_stream.py`：TanStack StreamChunk 型（Pydantic）定義・エンコード
    * `adk_to_tanstack.py`：ADK Event → StreamChunk 変換
    * `tanstack_to_adk.py`：UI入力 → ADK Content 変換
  * `stores/`

    * `run_store.py`：PendingAction 管理
    * `artifact_store.py`：結果保存/取得
  * `tools/`

    * `schema.py`：`preview_schema`
    * `sql.py`：`execute_sql`（承認付き）、`run_query_readonly`
    * `export.py`：`export_csv`（long-running / 外部結果待ち）
  * `domain/`

    * `models.py`：Artifact/RunState/ToolInput のドメインモデル
  * `config.py`：環境変数（モデル名、DBパス、上限制御）

## 6.2 ADK App 設計

### 6.2.1 Resumability 有効化

* 承認・外部実行・長時間処理を行うため、**Resumability を原則ON**とする。
* `App(..., resumability_config=ResumabilityConfig(is_resumable=True))`

### 6.2.2 Agent（LlmAgent）構成

* `instruction`：DBスキーマ、SQL生成ルール（SELECT only、LIMIT必須等）、ツール利用手順
* `tools`：

  * `preview_schema()`
  * `execute_sql(sql: str, limit: int, ...)`（承認が必要）
  * `export_csv(artifact_id: str, filename: str, ...)`（クライアント実行）
* ADK は Python 関数のシグネチャからスキーマを生成して tool として公開できる ([Google GitHub][1])

> 注：ADK の `output_schema` を使って厳密な構造化出力を強制したい場合、ドキュメント上「output_schema を設定すると agent は返信のみ可能で tool が使えない」制約があるため、このデモ要件（tool必須）とは両立しません。
> したがって本設計では **tool中心**で実装し、必要なら「最終応答を JSON で返すサブエージェント」等に分離する（拡張案）とします。

## 6.3 ツール設計

## 6.3.1 `preview_schema`

* 入力：なし
* 出力：テーブル一覧、カラム、簡単な説明
* 実装：DBメタデータから生成（固定のサンプルでも可）
* UI：LLMの回答内で参照するだけ（tool-result はログとして表示されても良い）

## 6.3.2 `execute_sql`（承認付き・読み取り専用）

### 入力

* `sql: str`（LLM生成）
* `max_rows: int = 100`（安全策）
* （必要なら）`reason: str`（LLMが「なぜこのSQLが必要か」を説明）

### 承認フロー（設計）

* **最初の呼び出し時**：ツールは実行せず、確認要求に遷移
* **承認後の再実行時**：読み取り専用DB接続でSQL実行 → Artifact を作成 → 結果返却

ADK の Tool Confirmation は、ツール実行を一時停止し、人間/外部システムの確認を得てから再開できる（remote response も可能）

### SQL安全対策（必須）

* 文字列検査（`SELECT` 以外禁止、`;` や `DROP/INSERT/UPDATE/DELETE` 禁止）
* `LIMIT` 強制付与（なければ拒否 or 自動付与）
* read-only 接続（SQLite なら URI で read-only 等）
* クエリタイムアウト（DBドライバ/実行レイヤで制御）
* 実行ログの記録（監査目的）

## 6.3.3 `export_csv`（クライアント実行・外部結果待ち）

### 入力

* `artifact_id: str`
* `filename: str`

### 実装方針

* ADK 側では Long Running Function として扱い、UIに実行を委譲する。
* Long Running Function のイベント検出と、後続の `FunctionResponse` 投入で処理継続できる。 ([Google GitHub][1])
* Resume を有効にしている場合、long-running response に `invocation_id` を含める必要がある（同一invocationの継続のため）。 ([Google GitHub][1])

### UI側（参考）

* `GET /api/data?id={artifact_id}` で表データ取得
* CSV化してダウンロード
* 成功/失敗を `/api/continuation` へ送信

---

# 7. API 設計

## 7.1 `POST /api/chat`（SSE）

### 目的

* TanStack AI の ConnectionAdapter から呼ばれ、StreamChunk を逐次返す。

### リクエスト（例）

* body（JSON）

  * `run_id?: string`
  * `messages?: UIMessage[]`（TanStack AI 形式）

### レスポンス

* `Content-Type: text/event-stream`
* data: `{StreamChunk JSON}` を逐次送出

### サーバ実装要点

* `run_id` 無し → 新規作成し最初の chunk で `id=run_id` を返す（既存挙動互換）
* ADK `Runner.run_async(session_id=run_id, user_id=..., new_message=...)` 実行
* Event → StreamChunk 変換して送出
* 承認待ち／クライアントツール待ちを検知したら、SSEを維持したまま ContinuationHub で待機し、受領後に Resume

## 7.2 `POST /api/continuation`

### 目的

* UIから承認/否認、クライアントツール結果を受領し、ADK を Resume

### リクエスト

```json
{
  "approvals": { "toolCallIdA": true, "toolCallIdB": false },
  "toolResults": { "toolCallIdC": "result string or json" }
}
```

### サーバ処理

* `toolCallId` で RunStore の PendingAction を引く
* `invocation_id` と ADK 側の応答先ID（確認なら `adk_request_confirmation` の function_call_id、外部ツールなら当該 toolCallId）を取得
* ADK Runner を `invocation_id` 指定で Resumeし、`new_message` に `FunctionResponse` を詰めて継続 ([Google GitHub][1])

## 7.3 `GET /api/data?id={artifact_id}`

* ArtifactStore から取得して返す（JSON）
* UI は表表示・CSV化などに利用

---

# 8. ストリーミング変換設計（ADK Event → TanStack StreamChunk）

## 8.1 変換ポリシー

* ADK Event の `content.parts[].text` → TanStack `text-delta` chunk（あるいは `content` chunk）
* `content.parts[].function_call` → TanStack `tool_call` chunk
* `content.parts[].function_response` → TanStack `tool_result` chunk
* 承認要求（ADK confirmation）

  * UIに必要なのは `approval-requested` chunk
  * ただし ADK の確認応答に必要なID等は RunStore に保持して内部で解決

## 8.2 重要：toolCallId 整合性

* UIは「tool-call part の `id`」で承認要求を突合するため、以下を保証：

  * `tool_call` chunk の `toolCall.id` と
  * `approval-requested` chunk の `toolCallId` が一致
* ADK confirmation の `adk_request_confirmation` が別IDを持つ場合でも、**内部マッピング**で吸収する（UIに余計なIDを見せない）

---

# 9. 非機能要件

## 9.1 セキュリティ

* SQL実行は read-only + 構文制限 + LIMIT強制
* PII/秘匿情報の混入を想定するなら、ログマスキング
* CORS、CSRF（同一オリジン前提なら最小化可）

## 9.2 信頼性

* Resume は at-least-once の可能性があるため、ツールは冪等化が重要（特に外部副作用）
* PendingAction に TTL を設定し、期限切れは明示的に失効

## 9.3 運用性

* RunStore/ArtifactStore のストレージを Redis/Postgres 等へ差し替え可能なインタフェース設計
* トレースID（run_id, invocation_id）をログに必ず出す

---

# 10. PydanticAI との機能対応表（google/adk-python 観点）

| 項目                   | PydanticAI（既存デモ相当）                                                           | ADK（google/adk-python）                                                                            | 設計上の注意                                         |
| -------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| エージェント定義             | `Agent(...)`（deps, system_prompt, validators 等）                              | `LlmAgent(...)` に instruction/tools を付与                                                           | ADK は multi-agent や runtime 概念が強い              |
| ツール定義                | `@agent.tool` / `Tool` / toolset                                             | Python関数を `tools=[func]` で渡すと自動で `FunctionTool` 化、シグネチャからスキーマ生成 ([Google GitHub][1])              | 型ヒント・docstringが実質スキーマの源泉                       |
| ストリーミング              | `run_stream()` / `run_stream_events()` + UIAdapter                           | `Runner.run_async()` が Event を逐次 yield（UIに流せる） ([Google GitHub][1])                               | 変換アダプタ層が必要（本設計の要）                              |
| UIプロトコル変換            | `UIAdapter` / `UIEventStream` が基底として提供                                       | 標準で TanStack 向けは提供されないため自作                                                                        | 既存デモの “tanstack↔pydantic” を “tanstack↔adk” に置換 |
| Human-in-the-Loop 承認 | Deferred Tools（`requires_approval` / `ApprovalRequired`）で run を止め、後続 run で継続 | Tool Confirmation（`require_confirmation` / request_confirmation）でツール実行を停止し、`FunctionResponse` で再開 | ADK は Resume/invocation_id と密接                 |
| 外部（フロント）実行ツール        | Deferred Tools（executed externally）で stop→結果投入で継続                            | Long Running Function の “外部更新→FunctionResponse→継続” を利用可能 ([Google GitHub][1])                     | TanStack の `tool-input-available` に接続するのが実装要点  |
| セッション/履歴             | message_history を自前保持（ストア設計は任意）                                              | session_id / invocation_id を中心に runtime が管理、Resume も提供                                            | 本設計では run_id≒session_id として整合                  |
| “停止→再開” の仕組み         | run を分割して再実行（履歴+deferred結果）                                                  | ResumabilityConfig + invocation_id で Resume                                                       | at-least-once になり得るので副作用ツール注意                  |
| 構造化出力（最終回答）          | output_type に Pydantic model を指定しやすい                                         | `output_schema` は可能だが、設定すると tools が使えない制約                                                         | 本件のように tool 前提だと「サブエージェント分離」等が必要               |
| マルチエージェント            | （可能だが）フレームワーク内蔵のオーケストレーションは相対的に薄い                                            | Multi-agent を前提にした構成・実装ガイドがある（Sequential/Parallel等）                                               | 将来拡張（SQL生成専任/説明専任など）に向く                        |
| 既知の制約                | （実装依存）                                                                       | Tool Confirmation は experimental で制約あり（例：特定 SessionService 非対応）                                   | 本番採用なら代替（独自承認ゲート）も検討                           |

---

# 11. 実装時の落とし穴と対策（重要）

1. **toolCallId の一貫性**

   * UIが承認要求を tool-call part の `id` で突合するため、ADK 側の confirmation ID とズレる場合は RunStore で必ず吸収する。

2. **Resume の冪等性（at-least-once）**

   * Resume ではツールが複数回実行され得るため、外部副作用がある処理は “実行済みチェック” を入れる。

3. **ADK `output_schema` 制約**

   * tool を使うエージェントに `output_schema` を付けない（付けると tool が使えない）。

4. **Tool Confirmation の実験的性質**

   * 本番前提なら、confirmation を ADK に依存しすぎず「バックエンドの承認ゲートで tool 実行を分岐」する案も用意する（フェイルセーフ）。

---

# 12. まとめ（この設計の要点）

* 既存デモの UI 体験（承認UI、クライアントツール、Artifactプレビュー）を維持するため、**TanStack StreamChunk** を契約として固定し、バックエンド側は **ADK Event を StreamChunk に変換するアダプタ**を中核に据える。
* 承認と外部実行は、ADK の **Tool Confirmation** と **Long Running Function + Resume** を使うことで「中断→再開」の自然なワークフローが組める。 ([Google GitHub][1])

---

必要であれば、次の粒度まで具体化できます（質問は不要で、そのまま提示可能です）：

* `POST /api/chat` の StreamChunk 送出仕様（chunk 種別ごとの JSON サンプル）
* ADK Event→TanStack chunk 変換のルール表（Eventパターン別）
* `execute_sql` の confirmation 実装パターン（require_confirmation方式／request_confirmation方式の比較）
* RunStore/ArtifactStore の RDB スキーマ案（Postgres想定）

[1]: https://google.github.io/adk-docs/tools-custom/function-tools/ "Overview - Agent Development Kit"

---

以下は、**添付の TanStack AI + PydanticAI デモと同等の UX（SSE ストリーミング / HITL 承認 / クライアントツール実行 / 継続実行）**を、**TanStack AI フロントエンドのまま**、バックエンドを **google/adk-python（ADK）** に差し替えて実装するための「具体レベル」の詳細設計です。
（※本設計は、添付プロジェクトの `/api/chat` + `/api/continuation` + `/api/data` という責務分割を踏襲します。）

---

## 1. 目的と到達点

### 1.1 目的

* 添付デモと同様に、以下を満たす：

  * **/api/chat** が **SSE** で `StreamChunk` を逐次返す（TanStack AI のプロトコル準拠）
  * LLM が `execute_sql` を呼ぶ際に **承認要求（approval-requested）** を挟む
  * LLM が `export_csv` を呼ぶ際に **承認→クライアント側 CSV ダウンロード（tool-input-available）→結果返却→会話継続** を実現
  * **/api/continuation** に承認結果・ツール結果を POST すると、**/api/chat の SSE 接続が継続して再開**する（添付デモ同様）
* ADK の Event ストリームを **TanStack AI の StreamChunk に変換**する “Adapter” を実装する

### 1.2 非目的（デモでは割り切る）

* マルチプロセス/マルチワーカでの厳密なストリーム再接続保証
* 強固な認証認可（デモでは任意）
* ADK Session の永続化（後述：tool confirmation の制約で InMemory を基本にする）

---

## 2. 全体アーキテクチャ

### 2.1 コンポーネント

* **Frontend（そのまま）**

  * TanStack AI React (`useChat`) + 添付デモの PendingApprovalsPanel / ToolInputPanel
* **Backend（FastAPI 想定）**

  * `/api/chat` : SSE で StreamChunk を配信
  * `/api/continuation` : 承認・ツール結果を受け取り、SSE 側を再開させる
  * `/api/data` : SQL 実行結果などの Artifact を返す（ToolInputPanel が利用）
* **ADK ランタイム**

  * `Runner.run_async(...)` が **Event** を yield するので、それを変換して SSE に流す
  * Event には `invocation_id` があり、確認や長時間ツールの再開に使用する
* **RunStore（状態）**

  * `run_id`（添付デモの概念）と ADK の `invocation_id`、保留中の承認・クライアントツール情報を保持
* **ContinuationHub（待ち合わせ）**

  * `/api/chat` の SSE ハンドラが「承認/結果待ち」で止まれるようにする（添付デモと同じ）

### 2.2 SSE 仕様（TanStack AI 準拠）

* Response header は `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `Connection: keep-alive`
* 送信形式は `data: {JSON}\n\n` の繰り返しで、最後に `data: [DONE]\n\n`
* StreamChunk のベースは `type, id, model, timestamp`

---

## 3. データモデル（バックエンド内部）

### 3.1 主要キー

* `run_id` : フロントが発行する会話セッション ID（添付デモ同様）
* `session_id` : ADK のセッション ID。デモでは **`session_id = run_id`** を推奨
* `invocation_id` : ADK の “1回の実行” を識別（再開に必要）

### 3.2 RunState（例）

```json
{
  "run_id": "r_123",
  "model": "gemini-2.0-flash",
  "session_id": "r_123",
  "user_id": "demo_user",
  "invocation_id": "inv_abc",
  "pending": {
    "approvals": {
      "toolCallId_1": {
        "tool_name": "execute_sql",
        "input": {"query":"..."},
        "adk_confirmation_call_id": "adk_confirm_call_xyz"
      }
    },
    "client_tools": {
      "toolCallId_2": {
        "tool_name": "export_csv",
        "input": {"artifact_id":"a_1"}
      }
    }
  }
}
```

---

## 4. API 設計（添付デモ互換）

## 4.1 `/api/chat`（SSE）

### Request（例：フロントから）

添付デモは `run_id` を body に入れているため、互換として以下を採用：

```json
{
  "run_id": "r_123",
  "messages": [
    {"role":"user","content":"records から 2025-12-24 の error を集計..."}
  ]
}
```

（TanStack AI の SSE プロトコルとしては `messages` と任意の `data` を持てます。デモでは `run_id` をトップレベル、または `data.run_id` に入れてもよいです。）

### Response（SSE）

例：

```
data: {"type":"content","id":"r_123","model":"gemini-2.0-flash","timestamp":...,"delta":"...","content":"...","role":"assistant"}

data: {"type":"tool_call",...}

data: [DONE]
```

---

## 4.2 `/api/continuation`（継続入力：承認/ツール結果）

### Request（例：承認）

```json
{
  "run_id": "r_123",
  "approvals": {
    "toolCallId_1": true
  }
}
```

### Request（例：クライアントツール結果）

添付デモは ToolInputPanel が `output`（JSON 文字列）と `state` を送っています：

```json
{
  "run_id": "r_123",
  "tool_results": {
    "toolCallId_2": {
      "output": "{\"type\":\"tool_result\",\"version\":1,...}",
      "state": "output-available",
      "errorText": null
    }
  }
}
```

### Response

```json
{"status":"ok"}
```

---

## 4.3 `/api/data`（Artifact 取得）

### Request（例）

`GET /api/data?run_id=r_123&artifact_id=a_1&mode=download`

### Response（例：inline）

```json
{
  "mode": "inline",
  "rows": [...],
  "columns": ["col1","col2"],
  "exported_row_count": 1000,
  "original_row_count": 53241
}
```

（S3 等を使う場合は signed-url モードも添付デモ互換で実装）

---

## 5. StreamChunk（TanStack 側）具体仕様（添付デモ互換）

`BaseStreamChunk` と type 一覧：`content / tool_call / tool-input-available / approval-requested / tool_result / done / error ...`

### 5.1 content

```json
{
  "type": "content",
  "id": "r_123",
  "model": "gemini-2.0-flash",
  "timestamp": 173...,
  "delta": "途中の追記",
  "content": "累積全文",
  "role": "assistant"
}
```

### 5.2 tool_call

TanStack の SSE 例でも `toolCall.function.arguments` は **JSON文字列**

```json
{
  "type": "tool_call",
  "id": "r_123",
  "model": "gemini-2.0-flash",
  "timestamp": 173...,
  "toolCall": {
    "id": "toolCallId_1",
    "type": "function",
    "function": {
      "name": "execute_sql",
      "arguments": "{\"query\":\"SELECT ...\"}"
    }
  },
  "index": 0
}
```

### 5.3 approval-requested（添付デモで PendingApprovalsPanel が反応）

```json
{
  "type": "approval-requested",
  "id": "r_123",
  "model": "gemini-2.0-flash",
  "timestamp": 173...,
  "toolCallId": "toolCallId_1",
  "toolName": "execute_sql",
  "input": {"query":"SELECT ..."},
  "approval": {
    "id": "toolCallId_1",
    "toolCallId": "toolCallId_1",
    "status": "pending",
    "needsApproval": true,
    "date": "2026-01-07T00:00:00Z",
    "metadata": {
      "hint": "Execute SQL?"
    }
  }
}
```

### 5.4 tool-input-available（添付デモで ToolInputPanel が表示）

```json
{
  "type": "tool-input-available",
  "id": "r_123",
  "model": "gemini-2.0-flash",
  "timestamp": 173...,
  "toolCallId": "toolCallId_2",
  "toolName": "export_csv",
  "input": {"artifact_id":"a_1"}
}
```

### 5.5 tool_result（添付デモは content が JSON 文字列で ToolResultPartView が解釈）

```json
{
  "type":"tool_result",
  "id":"r_123",
  "model":"gemini-2.0-flash",
  "timestamp": 173...,
  "toolCallId":"toolCallId_1",
  "content":"{\"type\":\"tool_result\",\"version\":1,...}",
  "role":"tool"
}
```

---

## 6. ADK Event → StreamChunk 変換仕様（最重要）

## 6.1 ADK Event の取り出し方（判定基準）

ADK 公式のイベント分類の基本：

* テキスト：`event.content.parts[0].text`
* ツール呼び出し要求：`event.get_function_calls()`（各要素に `.name`, `.args`）
* ツール結果：`event.get_function_responses()`（各要素に `.name`, `.response`）
* ストリーミング中断片か：`event.partial`
* 実行単位：`event.invocation_id`

---

## 6.2 Tool Confirmation（承認）の扱い

### 6.2.1 ADK の承認要求の発火方法

* ツールを `FunctionTool(..., require_confirmation=True)` でラップすると、yes/no の承認ステップを挟める

### 6.2.2 承認要求イベントの検出（実装の勘所）

ADK は内部的に `adk_request_confirmation` を function call として出し、そこに **元ツール呼び出し（originalFunctionCall）** と **ヒント**などを載せます（イベント例）。

したがって Adapter は：

1. `event.get_function_calls()` を走査
2. `call.name == "adk_request_confirmation"` を検出
3. `call.args.originalFunctionCall` から

   * `toolCallId`（元ツール呼び出し ID）
   * `toolName`
   * `toolInput`
     を抽出し、**approval-requested chunk** を emit
4. さらに、**ADK の confirmation call id**（`adk_confirmation_call_id`）を RunStore に保存

   * 後続の「承認結果」を ADK に返すとき、これが必要

### 6.2.3 承認結果の ADK への返し方（再開）

ADK の remote confirmation は `FunctionResponse`（name=`adk_request_confirmation`）を送って行う。
また、Resume 機能を使う場合は `invocation_id` を一致させて送る必要がある。

バックエンド内部では（ADK API サーバを使わず）：

* `/api/continuation` で受けた `approvals[toolCallId]=true/false` を
* `RunStore.pending.approvals[toolCallId].adk_confirmation_call_id` に引き当てて
* `runner.run_async(..., invocation_id=..., new_message=function_response)` を実行して再開

---

## 6.3 Client Tool（export_csv）の扱い（添付デモ互換）

### 6.3.1 ADK 側の選択肢

添付デモの「外部（ブラウザ）実行 → 結果を返して会話継続」は、ADK の **LongRunningFunctionTool** が最も噛み合います。
LongRunningFunctionTool は「開始だけして run を止め、後続でクライアントが intermediate/final FunctionResponse を返して続ける」ための仕組みです。

### 6.3.2 export_csv の推奨実装方針

* `export_csv` は ADK では **LongRunningFunctionTool** として登録
* さらに、添付デモ同様「CSV も確認してから」の要件があるため、`export_csv` も承認対象にする

  * 実装案 A（推奨）：`export_csv` 自体を `require_confirmation=True` で確認させた後に LongRunning に入る
  * 実装案 B（代替）：`export_csv` を **承認ツール**と**クライアントツール**に分割（`approve_export_csv` → `export_csv`）

※ADK のツールラップの可否はバージョン差分が出やすいので、A が難しければ B が確実です（デモとしての UX は同等にできます）。

### 6.3.3 tool-input-available を「いつ出すか」

添付デモは「承認後に ToolInputPanel が表示」されるため、以下の順を保証します：

1. `approval-requested`（export_csv）
2. 承認 true を受領
3. **その後に** `tool-input-available`（export_csv）

実装上は：

* export_csv の承認完了後に ADK を再開
* 再開した実行で export_csv が “開始” されたタイミング（= LongRunning の初回結果が出たタイミング）で `tool-input-available` を emit

  * `tool-input-available.input` には `{artifact_id: ...}` を入れる（ToolInputPanel が /api/data を叩ける）

### 6.3.4 クライアント結果の返却

ToolInputPanel が `/api/continuation` へ `tool_results[toolCallId]` を POST したら、

* それを ADK に `FunctionResponse` として返す（LongRunning の “final response” 相当）
* Resume 機能を使う場合は `invocation_id` を一致させて返す

---

## 7. ADK 実装設計

## 7.1 Resumability（再開）を有効化

ADK は ResumabilityConfig を用いて “resumable workflow” にでき、再開時に `invocation_id` を指定できる。

加えて、再開はイベント再生で行われるため、**ツールの冪等性**が重要です（同一 toolCallId の再実行に備える）。

## 7.2 SessionService の選定

Tool confirmation には既知制約があり、`DatabaseSessionService` と `VertexAiSessionService` は非対応です。
従ってデモでは **InMemorySessionService** を前提にします。

---

## 8. 実装スケルトン（添付デモを ADK 化する最小単位）

> 注意：下記は「構造・責務」を具体化するためのスケルトンです。`google-adk` の import 経路や型名はバージョンで差分が出る可能性があるため、最終的には採用バージョンの API に合わせて調整してください。

### 8.1 `main.py`（FastAPI）

```python
import asyncio, json, time
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse

app = FastAPI()

run_store = ...
continuation_hub = ...

@app.post("/api/continuation")
async def continuation(req: Request):
    body = await req.json()
    run_id = body["run_id"]
    continuation_hub.push(run_id, body)  # approvals/tool_results を SSE 側へ渡す
    return JSONResponse({"status": "ok"})

@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    run_id = body["run_id"]
    messages = body.get("messages", [])

    # セッション初期化（なければ作る）
    state = run_store.get_or_create(run_id)

    async def event_stream():
        adapter = TanStackAdkAdapter(run_id=run_id, run_store=run_store)

        # 1) ユーザ入力で ADK 実行開始
        async for chunk in adapter.run_from_user_messages(messages):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        # 2) 承認/クライアントツール待ちがあれば SSE を閉じずに待つ（添付デモ同様）
        while adapter.has_pending():
            # keep-alive（プロキシ対策）
            yield ": keep-alive\n\n"
            payload = await continuation_hub.wait(run_id)

            async for chunk in adapter.resume_from_continuation(payload):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
```

---

## 9. 具体シーケンス（SSE に流れる Chunk の並び）

## 9.1 `execute_sql`（承認→実行→結果→会話）

1. `tool_call (execute_sql)`
2. `approval-requested (execute_sql)`
3. ユーザが承認 → `/api/continuation {approvals: {toolCallId:true}}`
4. `tool_result (execute_sql)`（Artifact を含む JSON 文字列）
5. `content`（分析文）
6. `[DONE]`

## 9.2 `export_csv`（承認→tool-input-available→ブラウザ実行→結果→会話）

1. `tool_call (export_csv)`
2. `approval-requested (export_csv)`
3. ユーザが承認 → `/api/continuation approvals`
4. `tool-input-available (export_csv)`（ToolInputPanel 表示）
5. ユーザが Download → ToolInputPanel が `/api/data` 取得 → CSV 作成 → `/api/continuation tool_results`
6. `tool_result (export_csv)`（成功/失敗の JSON 文字列）
7. `content`（「ダウンロード開始しました」等）
8. `[DONE]`

---

## 10. 既存（PydanticAI）との機能対応表（ADK 化した場合）

PydanticAI の deferred tools は「承認が必要」「外部実行が必要」なツール呼び出しを **DeferredToolRequests** として返して run を止める仕組みです。
外部実行は `CallDeferred` 例外で表現されます。

以下は、添付デモの挙動を ADK で再現する場合の対応表です。

| 領域             | PydanticAI（添付デモ）                                                           | ADK（本設計）                                                                                                  | TanStack 側に出す Chunk                    | 実装メモ                                                                             |
| -------------- | -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | -------------------------------------- | -------------------------------------------------------------------------------- |
| ストリーミング本文      | `Agent.run_stream_events()` のテキスト差分                                        | `Runner.run_async()` の `event.content.parts[0].text` と `event.partial`                                    | `content`                              | `delta` と `content` を Adapter 内で累積                                               |
| ツール呼び出し通知      | `FunctionToolCallEvent`                                                    | `event.get_function_calls()`（`.name`, `.args`）                                                            | `tool_call`                            | `arguments` は JSON 文字列化（TanStack 例）                                              |
| サーバツール結果       | `FunctionToolResultEvent`                                                  | `event.get_function_responses()`（`.name`, `.response`）                                                    | `tool_result`                          | content は添付デモ互換の JSON 文字列にする                                                     |
| HITL 承認（SQL 等） | `requires_approval=True` → `DeferredToolRequests`                          | `FunctionTool(..., require_confirmation=True)`                                                            | `approval-requested`                   | ADK は `adk_request_confirmation` を function_call として出す（originalFunctionCall を含む） |
| 承認の返却          | `/api/continuation approvals` → `DeferredToolResults` で再開                  | `/api/continuation approvals` → `FunctionResponse(name=adk_request_confirmation)` を `invocation_id` 付きで再開 | 再開後に `tool_result/content`             | tool confirmation には SessionService の制約あり                                        |
| クライアントツール（CSV） | `CallDeferred` → `tool-input-available` → `/api/continuation tool_results` | `LongRunningFunctionTool` で “外部（ブラウザ）実行” を表現                                                              | `tool-input-available` → `tool_result` | ToolInputPanel は `input.artifact_id` を見て /api/data を叩く                           |
| 実行再開の整合性       | RunStore + Deferred handshake                                              | `invocation_id` を用いた再開（ResumabilityConfig）                                                                | 同一 SSE を継続                             | resume はイベント再生なので冪等性必須                                                           |

---

## 11. 実装上の重要注意点（失敗しやすいポイント）

1. **承認レスポンス/長時間ツールレスポンスに invocation_id を付ける**

* Resume 機能を有効にした場合、`invocation_id` が一致しないと「新しい invocation」として扱われ、期待通り再開できません。

2. **tool confirmation の SessionService 制約**

* `DatabaseSessionService` / `VertexAiSessionService` が使えないため、デモでは InMemory 前提になります。

3. **冪等性（同一 toolCallId の二重実行）**

* resume はイベント再生で動くため、ツールが再実行され得ます。toolCallId をキーにキャッシュ/排他を入れてください。

---

## 12. 次にやるべき実装タスク（最短ルート）

* [ ] `TanStackAdkAdapter`（Event→Chunk 変換 + pending 判定 + resume 実行）
* [ ] `RunStore` 拡張（invocation_id / pending approvals / pending client tools / confirmationCallId の保持）
* [ ] `ContinuationHub`（run_id 単位の await）
* [ ] `execute_sql`（FunctionTool require_confirmation）
* [ ] `export_csv`（承認→LongRunning→tool-input-available→final tool_result の連携）
* [ ] `/api/data`（ArtifactStore 互換）

