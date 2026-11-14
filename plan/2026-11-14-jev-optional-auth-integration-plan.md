# Plan: tích hợp Jev tùy chọn và lệnh `ai auth`

- Ngày: 2026-11-14 (bản sửa sau review cùng ngày).
- Trạng thái: P0 (.gitignore), P1, P2, P3 đã triển khai trong `cli/` và các skill (2026-11-14), test bằng jg/store/HTTP giả; P0.5 (spike đo token) và kiểm thử với key thật chưa chạy.
- Phạm vi: bộ skill `ai-main`, `ai-auto`, `ai-orchestrate`; CLI tùy chọn mới `ai`; skill `jevgrep` không nằm trong repo, được installer `npx -y github:0x01001/ai-skill` cài kèm thẳng từ upstream (bỏ được bằng `--no-jev`).
- Người yêu cầu/nghiệm thu: chủ repository.
- Mục tiêu: giảm context/token của model chính và thời gian khám phá code khi có ích, không đánh đổi correctness, bảo mật hoặc khả năng chạy không có Jev.
- Thay đổi so với bản đầu:
  - Thêm phương án dùng `jg` với credentials tạm qua `XDG_CONFIG_HOME`. Bản đầu kết luận sai rằng `jg` không nhận override nào từ môi trường.
  - Thêm P0.5 spike để đo token trước khi viết bất kỳ dòng CLI nào.
  - Cắt phạm vi xuống MVP.
  - Consent một lần trong `ai auth`.
  - Cài CLI bằng `pipx` từ git.
  - Bỏ `skills/jevgrep` khỏi repo. Installer một lệnh `npx -y github:0x01001/ai-skill` cài ba skill `ai-*` rồi cài `jevgrep` thẳng từ upstream, để `npx skills update` luôn kéo bản mới nhất. Không dùng GitHub Actions hay script đồng bộ (mục 10.1, Phụ lục B).
  - Chỉ còn ba lệnh: `ai auth`, `ai logout`, `ai doctor`. Skill `ai-*` gọi Jev qua shim `jg` thay cho `ai jev search` (mục 5.2, 8.1).
  - Không có loại trừ theo repo: phạm vi cài skill (global hay theo project) đã quyết định skill có mặt ở repo nào; root để search do agent lấy từ cwd. Chỉ giữ loại trừ theo nhóm file (`.env`, private key...). User quyết định ngày 2026-11-14 (mục 6.4).
  - Bỏ `ai auth skip`: trùng với trạng thái chưa chạy `ai auth`. Tắt Jev sau khi đã auth bằng `ai logout` (quyết định 2, mục 5.4).
  - `ai auth` áp dụng luôn cho skill `jevgrep`: CLI `ai` kèm shim `jg` lấy key từ keyring, nên không cần `jg auth` riêng (quyết định 9, mục 6.5).
  - Sửa các mâu thuẫn về validation, budget, deadline, retry và các dữ kiện API.

## 1. Quyết định chính

1. **Jev là công cụ chọn context tùy chọn, không thay model của agent.** Model hiện tại tiếp tục hiểu yêu cầu, chọn lane, lập kế hoạch, viết code, debug, review và kết luận hoàn thành.
2. **Luồng cũ là mặc định.** Không key, chưa chạy `ai auth` hoặc đã `logout`, thiếu CLI, thiếu `jg`/Node hoặc Jev lỗi thì quay về đúng luồng cũ. Không bắt auth, không tự cài dependency, không chặn tác vụ. Không có lệnh `skip`: chưa chạy `ai auth` chính là trạng thái không dùng Jev; đã auth rồi muốn tắt thì `ai logout`.
3. **Đo trước, xây sau.** P0.5 dùng `jg` nguyên bản để đo token của model chính trên các task discovery thật. Không đạt ngưỡng thì dừng feature và không viết CLI.
4. **Engine đề xuất cho MVP: `jg` (jevgrep) pin version, nhận key qua credentials tạm.** Đây chính là engine được đo ở spike, cần ít code nhất và giữ được semantic discovery nhiều tầng. User xác nhận đánh đổi về credentials tạm tại gate P0.5. Nếu user từ chối, hoặc `jg` không tương thích, dùng engine dự phòng native reranker (Phụ lục A).
5. **`ai auth` là lệnh terminal thật,** được installer `npx -y github:0x01001/ai-skill` cài sẵn (pipx, từ chính bản package đó) cùng `jg` (`npm install --global @dzhng/jevgrep@latest`); sau khi cài, người dùng chỉ còn chạy `ai auth` và nhập key (user chốt 2026-11-14). Không thay bằng slash command `/ai-auth`, vì nhập key ẩn cần TTY thật. Cài thiếu CLI (`--no-jev`, `npx skills add 0x01001/ai-skill`, hoặc bước pipx lỗi) vẫn đủ cho luồng cũ.
6. **API key nằm trong secret store của hệ điều hành và được mã hóa at-rest.** Không có file plaintext lâu dài và không tự phát minh thuật toán mã hóa. Ngoại lệ duy nhất, có giới hạn rõ: file credentials tạm trong vòng đời tiến trình `jg` (mục 6.2).
7. **Consent một lần trong `ai auth`,** gắn với provider + endpoint + policy version. Không có danh sách repo loại trừ: phạm vi cài skill quyết định repo nào dùng; muốn tránh một repo thì cài skill theo project ở các repo cần dùng, hoặc `ai logout` (mục 6.4). Đổi provider hoặc endpoint thì phải consent lại.
8. **`jevgrep` được cài kèm thẳng từ upstream bằng installer một lệnh; repo ai-skill không chứa nó.**
   - `npx skills add` chỉ nhận một nguồn và không có cơ chế phụ thuộc (đã thử: nguồn thứ hai bị bỏ qua không báo lỗi). Vì vậy repo có `package.json` + `bin/install.mjs`: `npx -y github:0x01001/ai-skill <cờ>` chạy `npx skills add 0x01001/ai-skill --skill '*' <cờ>` rồi `npx skills add dzhng/jevgrep --skill jevgrep <cờ>`. `--no-jev` bỏ lượt hai (và cả phần Jev bên dưới). `--skill jevgrep` là bắt buộc vì repo upstream chứa 32 skill.
   - Sau hai lượt đó installer cài phần Jev (best effort): CLI `ai` + shim `jg` bằng pipx (tự cài pipx bằng `python -m pip install --user pipx` nếu thiếu, rồi `pipx ensurepath`) và `jg` bằng npm khi Node ≥ 22. Lỗi ở phần này chỉ in cảnh báo kèm lệnh làm tay, không đổi exit code của phần skill. `--no-jev` bỏ toàn bộ phần Jev (cả skill `jevgrep`); đó là cờ riêng duy nhất. Cờ `--list` của `skills add` chỉ liệt kê nên cũng không cài gì cho Jev.
   - Lock của người dùng ghi nguồn `dzhng/jevgrep`, nên `npx skills update` cập nhật cùng lúc ba skill `ai-*` (từ `0x01001/ai-skill`) và `jevgrep` lên bản mới nhất của upstream.
   - Lý do: `npx skills update` lấy mỗi skill từ đúng nguồn đã ghi lúc cài. Nếu ai-skill đi kèm một bản copy thì bản đó chỉ mới khi repo ai-skill được cập nhật (Phụ lục B).
   - `skills/jevgrep/` không còn ở trong repo (đã gỡ khỏi checkout ngày 2026-11-14) và không được thêm lại dưới bất kỳ dạng nào: không vendor, không submodule, không patch, không script đồng bộ, không GitHub Actions. Nguồn duy nhất của `jevgrep` là `dzhng/jevgrep`, cài cùng lúc với ai-skill qua installer.
   - Đánh đổi user đã chọn: `jevgrep` là bản upstream nguyên văn. Có CLI `ai` và đã chạy `ai auth` thì nó chạy qua shim `jg` với key và guard của `ai auth` (quyết định 9); không có CLI thì nó tự cài `jg@latest`, nhắc `jg auth`, credentials riêng, nằm ngoài guard của ai-skill (mục 15).
   - CLI `ai` và các skill `ai-*` không gọi `jg auth`, không đọc credentials của `jg auth`, không yêu cầu skill `jevgrep`.
   - `skills-lock.json` không commit.
9. **`ai auth` dùng chung cho `jevgrep`: shim `jg` trong CLI `ai`, không cần `jg auth`.**
   - Skill `jevgrep` gọi thẳng `jg` từ shell của agent; `jg` chỉ đọc key từ `credentials.json` trong thư mục config, không nhận key qua biến môi trường (mục 2.2). Vì vậy `ai` không thể "đưa key" cho `jevgrep` bằng env.
   - Package CLI expose thêm console script `jg` (shim). Shim đứng trước `jg` thật trên PATH, kiểm tra gate (enabled, consent, version), dựng credentials tạm từ keyring qua `XDG_CONFIG_HOME` (đúng cơ chế 6.2), gọi `jg` thật rồi dọn. `jg auth` qua shim bị chặn và chỉ sang `ai auth` (mục 6.5).
   - Kết quả: một lần `ai auth` (key, consent) dùng cho cả các skill `ai-*` và skill `jevgrep` (đều gọi Jev qua shim `jg`); `ai logout` tắt Jev (shim không chạy `jg` nữa); key không nằm plaintext lâu dài trong `%APPDATA%\jevgrep`.
   - Không chọn: `ai auth` ghi key thẳng vào `credentials.json` của `jg`. Cách này đơn giản nhưng để key plaintext lâu dài (trái JEV-04), và `logout` vẫn không chặn được `jg`.
   - Không có lệnh hay câu hỏi bật/tắt riêng cho `jevgrep`: `ai auth` là bước duy nhất. Sau khi lưu key và consent, `ai auth` tự thiết lập cho `jevgrep` (kiểm tra shim đứng trước `jg` của npm trên PATH, tự sửa thứ tự PATH của user nếu cần) và in `jevgrep: ready`.
   - Shim tự hành xử theo trạng thái `ai auth`: đã auth có key → dùng key đó, chặn `jg auth`; chưa từng chạy `ai auth` hoặc đã `logout` → không có key Jev nên không dùng `jg`: mọi lệnh trả fallback `no_key` (exit 3) mà không chạy `jg` thật, `jg auth` vẫn bị chặn; người gọi chạy workflow như cũ. Người chỉ cài skill mà không cài CLI `ai` vẫn có `jevgrep` hành xử như upstream.
10. **Đo toàn tác vụ,** gồm cả Jev, retry và fallback. Không biến số liệu tiết kiệm của bài viết hay benchmark upstream thành cam kết cho repo này.

## 2. Nguồn nghiên cứu và kết luận đã xác minh

### 2.1 Bài viết và tài liệu Jev

| Nguồn | Điều áp dụng | Giới hạn cần giữ |
| --- | --- | --- |
| [200Lab: Jev lọc tin cho Grok Bot](https://200lab.io/blog/jev-grok-bot-decisions.md) | Lọc/gom cụm cục bộ trước khi hỏi (2.418 → 520 cụm), batch nhiều câu (khoảng 450 câu/request), chép nội dung ứng viên thẳng vào câu hỏi, cache 4 ngày, trần cứng 30 request mỗi lần, fallback xếp hạng nội bộ | 193 → 6 request là tác vụ của tác giả, không phải dự báo cho coding agent. Nhận định "tiếng Anh chính xác nhất" chưa tìm thấy trong docs hiện tại |
| [200Lab: Jev và hệ thống agent](https://200lab.io/blog/tim-hieu-ve-jev-he-thong-ai-agent-se-thay-doi-ra-sao.md) | Tách phán đoán có đáp án giới hạn khỏi việc sinh nội dung và thực thi; đo kết quả end-to-end | Model chọn không được tự cấp quyền hoặc thực thi hành động |
| [Models](https://docs.typesafe.ai/models.md) | Có `jev-1.14.0` và `jev-1.13.0`, cùng giới hạn 64k/request và 32k/question (tính cả `state`), cùng giá. Alias `jev-latest`, `jev-preview` hiện trỏ `jev-1.14.0` và sẽ đổi khi có release mới. Không giới hạn số câu. Pin version khi đã hiệu chỉnh. Rate limit tính theo organization (token/s, request/s), vượt thì nhận 429 | Long context bật bằng header `TypeSafe-Danger-Zone-Long-Context` (262k), chậm và kém chính xác hơn: không gửi header này. Changelog 1.14 ghi "Better multilingual support" |
| [Model jaggedness](https://docs.typesafe.ai/model-jaggedness.md) | Câu hỏi nguyên tử, trực tiếp; lọc trước, chỉ gửi state cần thiết; đếm, so ngày và tính toán làm bằng code | Các điểm yếu: đọc quá sát chữ, indirection, state thừa, nội dung adversarial/prompt injection, chỉ dẫn mâu thuẫn với criteria, thứ tự option của Choice. Jev không được huấn luyện để sinh văn bản |
| [Noul](https://docs.typesafe.ai/primitives/noul.md), [Confidence](https://docs.typesafe.ai/confidence.md) | Mỗi ứng viên một Noul. `noul` ∈ [0,1] đã là xác suất, không có field `confidence` riêng; cần thì tự tính `\|2p − 1\|` | Không coi score là bảo đảm correctness; ngưỡng phải lấy từ dữ liệu thực |
| [System One API](https://docs.typesafe.ai/api/system-one.md) | Bearer auth, `POST /v1/systemone`. Body gồm `model`, `state`, `questions` (bắt buộc, ít nhất 1 câu) và `assets` (tùy chọn). **`questions` là map với key là ID do client đặt; không có field `id`.** Mỗi câu có dạng `{type, instructions, criteria}`; với Noul, `criteria: {true, false}` là tùy chọn. Response gồm `model`, `answers` (key trùng với key của `questions`) và `usage{input_tokens, output_tokens}` | OpenAPI của trang này chỉ liệt kê 200/400/422 |
| [Errors](https://docs.typesafe.ai/api/errors.md) | Các mã: 400 (gồm `max_tokens_exceeded`), 401, 413 (body > 32 MiB, tính cả trước và sau giải nén), 415, 422, 429, 529 "Overloaded". Với 429/529: retry bằng exponential backoff | 403 không có trong docs. `Retry-After` không được bảo đảm có; trang Models chỉ nói SDK chính thức theo header này khi response có |
| [Coding agents](https://docs.typesafe.ai/introduction/coding-agents.md) | Jev không phải drop-in thay LLM đứng sau Claude Code/Codex | Không đưa Jev vào tier writer/reviewer trong `models.md` |
| SDK chính thức (Python `typesafe_sdk`, JS `@typesafe-ai/sdk`, `@ai-sdk/typesafe-ai`) | Có sẵn và tự retry 429 | MVP không dùng: search đi qua `jg`, còn validation chỉ cần một request stdlib. Tránh thêm dependency và retry ngầm |

Giá native niêm yết lúc đọc: **0,042 USD/triệu input token, output token miễn phí**. Đây chỉ là ảnh chụp giá ngày lập plan; không áp cho gateway khác và không đóng đinh vào logic tính phí.

Tài liệu [Legal](https://docs.typesafe.ai/legal.md): có ZDR cho khách hàng enterprise; dữ liệu request không dùng để huấn luyện. Không suy diễn rằng mọi tài khoản hay mọi gateway đều zero retention.

### 2.2 `jevgrep` (`jg`): engine dùng được, credential store thì không

Đã đối chiếu upstream tại commit `52a499c628ca64a159f7b6166c52fcfa51e9e1ec` (HEAD của `main` ngày 2026-11-12), CLI `@dzhng/jevgrep` 0.8.5, bin `jg`, Node ≥ 22, license MIT:

- [`auth.ts`](https://github.com/dzhng/jevgrep/blob/52a499c628ca64a159f7b6166c52fcfa51e9e1ec/apps/cli/src/auth.ts):
  - `save()` ghi `JSON.stringify(credentials)` vào `.credentials-<uuid>.json` rồi rename thành `credentials.json`.
  - `loadCredentials()` chỉ `JSON.parse`, không giải mã, nên **key nằm plaintext**.
  - Directory mode `0700`, file mode `0600`. Đây là access control trên POSIX, **không phải encryption**.
- [`CLI README`](https://github.com/dzhng/jevgrep/blob/52a499c628ca64a159f7b6166c52fcfa51e9e1ec/apps/cli/README.md): Windows dựa vào ACL của user profile. Config ở `%APPDATA%\jevgrep`, cache ở `%LOCALAPPDATA%\jevgrep`.
- **Thư mục config đổi được cho từng tiến trình.** [`storage.ts`](https://github.com/dzhng/jevgrep/blob/52a499c628ca64a159f7b6166c52fcfa51e9e1ec/packages/core/src/storage.ts):
  - Đọc `XDG_CONFIG_HOME` trước tiên nếu là đường dẫn tuyệt đối, trên **mọi nền tảng, kể cả Windows**; sau đó mới tới `APPDATA`.
  - Cache theo `XDG_CACHE_HOME`, tách riêng.
  - Không có flag `--config`.
  - Hệ quả: tiến trình cha có thể trỏ `jg` vào một thư mục credentials tạm, chỉ áp cho tiến trình con.
- `jg` bỏ qua override API key, endpoint và model từ biến môi trường:
  - Biến môi trường duy nhất `jg` đọc: `CI`, `ComSpec`, `XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, `APPDATA`, `LOCALAPPDATA`.
  - SDK bên dưới chỉ đọc `TYPESAFE_AI_API_KEY` khi không có key được truyền vào, mà `jg` luôn truyền key đã lưu.
- [`providers.ts`](https://github.com/dzhng/jevgrep/blob/52a499c628ca64a159f7b6166c52fcfa51e9e1ec/packages/core/src/providers.ts):
  - Có 4 preset và 1 custom endpoint.
  - Tất cả đi qua `@ai-sdk/typesafe-ai@3.0.8`: `POST <base>/systemone` cùng schema native, không dùng chat completions.
  - Timeout 60 s cho `typesafe`, 15 s cho các gateway.
  - Rate budget phía client (100k token/s, 40 request/s) chỉ áp cho `typesafe`.
- `jg` gửi Noul **không kèm `criteria`**. README cảnh báo gateway nào bắt buộc `criteria.true` sẽ trả 422; gateway đó không dùng được với `jg`.
- Retry và giới hạn mặc định:
  - Concurrency mặc định 32, `--max-requests` mặc định 1000 (CLI).
  - Gặp 429 thì theo `retry-after` (giây hoặc HTTP date); thiếu header thì chờ 1 s.
  - Thông báo lỗi từ provider được redact key.
- [`cache.ts`](https://github.com/dzhng/jevgrep/blob/52a499c628ca64a159f7b6166c52fcfa51e9e1ec/packages/core/src/cache.ts):
  - Namespace gồm model, provider, endpoint, protocol, policy/parser/prompt version.
  - Key là sha256; TTL 7 ngày, tối đa 256 MiB.
- `@repo/core` là package private; chỉ CLI được publish.

Kết luận: dùng `jg` làm engine được, với điều kiện key không nằm lâu dài trong `%APPDATA%\jevgrep` hay `~/.config/jevgrep`. Không dùng `jg auth` và không dùng credential store của nó.

### 2.3 Hiện trạng repo

- `ai-main/SKILL.md:16`: đã có nhánh scout `t1` khi cần khảo sát nhiều file. Đây là điểm chèn discovery tùy chọn.
- `ai-auto/SKILL.md:24,31–44`: Authorized effects và Gate 2 Prepare.
- `ai-orchestrate/SKILL.md:53,60,67,73`: worker brief, verification, arbiter, report. Giữ nguyên các gate này.
- `skills/jevgrep/SKILL.md` và `skills-lock.json` (trạng thái lúc review):
  - Từng có trong checkout nhưng chưa được track. SKILL.md giống hệt upstream (chỉ khác CRLF). Lock file (version 1) chỉ có record của jevgrep, nguồn `dzhng/jevgrep`.
  - Nếu commit vào `skills/`, lệnh `npx skills add 0x01001/ai-skill` sẽ cài nó như một bản copy, trùng tên với bản upstream, và chỉ cập nhật theo repo ai-skill.
  - → Đã gỡ khỏi checkout (2026-11-14). `jevgrep` được cài cùng ai-skill từ upstream qua installer (quyết định 8).
  - Nội dung upstream tự cài `@latest`, nhắc chạy `jg auth`, và buộc subagent bắt đầu bằng `jg`. Đây là hành vi của skill upstream được cài kèm nguyên văn; ai-skill không sửa nó.
- Repo chưa có CLI hay package. Python hiện chỉ dùng stdlib (`sessions.py`), nên `keyring` sẽ là dependency bên thứ ba đầu tiên, và chỉ nằm trong CLI tùy chọn.
- Reference hiện tại hardcode đường dẫn `.claude/skills` (`ai-orchestrate/SKILL.md:59`, `references/sub-sessions.md:11,34`). CLI mới phải resolve từ executable đang chạy, không suy ra từ checkout hay vị trí worktree.

## 3. Phương án và lựa chọn

| Phương án | Ưu điểm | Điểm không phù hợp | Quyết định |
| --- | --- | --- | --- |
| Bọc nguyên `jg auth` + `jg` search | Ít code nhất | Key plaintext lâu dài trong `%APPDATA%\jevgrep`; `logout` và consent của ai-skill không kiểm soát được credentials của `jg` | Không chọn |
| **`jg` pin version + credentials tạm qua `XDG_CONFIG_HOME`.** Key lấy từ OS keyring, ghi vào thư mục riêng tư chỉ trong vòng đời tiến trình `jg`, xóa trong `finally` | Giữ nguyên semantic discovery nhiều tầng, tree-sitter, cache, retry 429 của `jg`. Là đúng engine được đo ở P0.5. Phần code của ta chỉ còn auth và runner | Key plaintext trên đĩa trong lúc search chạy (vài giây đến vài chục giây; nếu crash thì còn tới lần dọn kế tiếp). Cần Node ≥ 22 và `jg` do user tự cài. Phụ thuộc schema `credentials.json` của version đã pin | **Đề xuất cho MVP;** user xác nhận đánh đổi tại gate P0.5 |
| Fork/vendor core Jevgrep rồi thay secret store | Có sẵn retrieval, parser, transport | Core là package private; kéo theo TypeScript/Node/parser và nghĩa vụ cập nhật | Không chọn |
| CLI Python + HTTP client riêng, rerank ứng viên lấy từ grep | Không có plaintext nào; không cần Node | Mất semantic discovery: chỉ xếp hạng lại kết quả lexical, có thể bỏ sót code không trùng từ. Phải tự viết prefilter, batching, validator, cache | Dự phòng (Phụ lục A), dùng khi user từ chối credentials tạm hoặc `jg` không tương thích |

Các phương án dùng chung phần auth, keyring, logout, consent và fallback (mục 5–6); chỉ engine search khác nhau. Tra exact path hoặc symbol thì vẫn dùng grep/LSP, không gọi Jev.

## 4. Hợp đồng hành vi

| ID | Yêu cầu | Chứng minh nghiệm thu |
| --- | --- | --- |
| JEV-01 | Không cài CLI, chưa auth, không key, thiếu `jg`/Node: các skill vẫn chạy như trước | Chạy task discovery và sửa file trong môi trường không CLI/key/Node, network tới Jev bị chặn: 0 auth prompt, 0 install, 0 call Jev |
| JEV-02 | `ai auth` cho chọn provider, nhập key ẩn, consent, hoặc thoát giữa chừng (Ctrl+C) mà không đổi cấu hình | Chạy executable đã cài bằng pipx trên terminal thật |
| JEV-03 | `logout` đưa về đúng trạng thái như chưa từng chạy `ai auth`: xóa key trong secret store, bỏ consent, giữ PATH cấp user để `jg` vẫn đi qua shim; xóa key lỗi thì vẫn disabled và báo rõ | Sau `logout`: secret store không còn entry, 0 request từ các skill `ai-*`, shim trả `no_key` và không chạy `jg` thật; giả lập store lỗi: 0 request, có thông báo; muốn bật lại phải chạy `ai auth` và nhập key |
| JEV-04 | Không có key plaintext lâu dài trong repo, config, cache, argv, env của tiến trình con, log hay transcript. File credentials tạm chỉ tồn tại trong thư mục riêng tư, trong vòng đời `jg` | Scan sentinel key sau lần chạy bình thường, sau timeout (kill), và sau crash rồi lần chạy kế tiếp (dọn file mồ côi): không tìm thấy |
| JEV-05 | Chỉ upload khi có consent hợp lệ cho provider/endpoint/policy hiện tại. File thuộc nhóm loại trừ (6.4): 0 upload | Thử consent cũ sau khi đổi endpoint, `.env`/private key trong root: 0 upload, luồng cũ tiếp tục |
| JEV-06 | Jev chỉ hỗ trợ chọn context; không bỏ gate, không hạ tiêu chí Done | Tình huống auth/data loss/prompt injection không làm thay đổi ownership, permissions, tests, arbiter hay risk tier |
| JEV-07 | Lỗi provider, keyring, `jg`, budget hay timeout không chặn công việc chính | Fault injection từng nhóm lỗi: fallback có reason code, luồng cũ tiếp tục |
| JEV-08 | Đo tiết kiệm trên kết quả hoàn chỉnh, không chỉ token của Jev | Báo cáo A/C (thêm B nếu dùng engine native) gồm usage của model chính, usage Jev, retry, fallback, latency, task success |
| JEV-09 | Cài local/global, checkout, worktree, resume đều không phụ thuộc đường dẫn cứng | Chạy cùng scenario từ các vị trí này, kể cả đường dẫn có dấu cách |
| JEV-10 | Đổi provider, endpoint hay key là thao tác explicit và phải consent lại; không âm thầm đổi nơi nhận dữ liệu | Endpoint đổi làm consent cũ mất hiệu lực; không fallback sang provider thứ hai |
| JEV-11 | Không tự cài `jg` hay Node. `jg` thiếu hoặc khác version được hỗ trợ: luồng cũ, kèm reason code | Môi trường không Node; môi trường có `jg` khác version đã pin |
| JEV-12 | CLI `ai` và các skill `ai-*` không đọc và không dùng credentials từ `jg auth` có sẵn của user | Đặt `%APPDATA%\jevgrep\credentials.json` chứa key giả khác: request dùng key của `ai auth`, hoặc không có request nào khi chưa auth hoặc đã `logout` |
| JEV-13 | Repo ai-skill không chứa `jevgrep`; installer cài nó từ upstream, `--no-jev` bỏ qua được. Có hay không có `jevgrep`, các skill `ai-*` vẫn chạy đúng và không cài `jg`, không hỏi `jg auth` | `npx skills add <checkout> --list` chỉ có ba skill `ai-*`; installer cài 3 + 1 skill, `--no-jev` chỉ cài 3 và không cài CLI `ai` hay `jg`, `--skill` bị từ chối; scenario khi có và không có `jevgrep`: luồng `ai-*` không đổi, 0 prompt auth do skill `ai-*` gây ra |
| JEV-14 | `jevgrep` được cài cùng ai-skill qua installer với nguồn `dzhng/jevgrep`, nên `npx skills update` đưa nó lên bản mới nhất của upstream, không đi qua repo ai-skill | Chạy installer vào project thử: `skills-lock.json` ghi `source: dzhng/jevgrep` cho `jevgrep` và `0x01001/ai-skill` cho ba skill kia; `npx skills check` lấy từ cả hai nguồn (đã pass ngày 2026-11-14) |
| JEV-15 | Sau `ai auth` (không thêm bước nào), skill `jevgrep` chạy `jg` bằng key của `ai auth` mà không cần `jg auth`; chưa consent, version không hỗ trợ: shim không gọi `jg` thật, không network, báo dùng luồng thường; `jg auth` qua shim không chạy | Agent chạy scenario của skill `jevgrep` với `%APPDATA%\jevgrep` trống: search thành công bằng key của `ai auth`; sentinel scan sạch; lặp lại khi chưa consent: 0 request; chưa từng chạy `ai auth` hoặc sau `logout`: shim chuyển nguyên lệnh sang `jg` thật; `jg auth` sau khi đã auth trả hướng dẫn `ai auth` |

## 5. CLI `ai auth` và onboarding

### 5.1 Cài đặt

```text
# người dùng: một lệnh cài skill + jevgrep + CLI ai + jg, rồi chỉ còn `ai auth`
npx -y github:0x01001/ai-skill -a claude-code -y -g
ai auth            # trong terminal mới

# làm tay (tương đương phần Jev của installer)
pipx install "git+https://github.com/0x01001/ai-skill#subdirectory=cli" && pipx ensurepath
npm install --global @dzhng/jevgrep@latest   # Node >= 22; bản nào cũng được, cập nhật là việc của jevgrep upstream

# phát triển từ checkout
pipx install --editable ./cli
```

- Installer cài CLI từ thư mục `cli/` trong chính bản package mà `npx` tải về (`package.json` `files` gồm `cli/pyproject.toml`, `cli/README.md`, `cli/src`, không có tests/cache), nên CLI cùng phiên bản với skill vừa cài. Chạy lại installer để cập nhật CLI và `jg`; `npx skills update` chỉ cập nhật skill.
- Người dùng cài; agent không tự chạy lệnh cài nào (kể cả installer).
- Lệnh cài skill: `npx -y github:0x01001/ai-skill -a claude-code -y [-g]` (`--no-jev` bỏ mọi phần Jev: skill `jevgrep`, CLI `ai`, `jg`; đây là cờ riêng duy nhất của installer, user chốt 2026-11-14). Lệnh cũ `npx skills add 0x01001/ai-skill` vẫn dùng được và chỉ cài ba skill. Một lệnh `npx skills update` cập nhật cả hai nguồn skill.
- Package có hai console script: `ai` và shim `jg` (mục 6.5). Sau `pipx install`, thư mục bin của pipx phải đứng trước thư mục bin global của npm trong PATH để `command -v jg` trả về shim; `ai auth` và `ai doctor` kiểm tra điều này.
- Console entry point `ai` theo [PyPA](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#creating-executable-scripts).
- Nếu PATH đã có `ai` của công cụ khác: dùng virtualenv riêng, không ghi đè. Installer đọc `pipx list --json` và thư mục bin của pipx trước khi cài: `ai`/`jg` thuộc package pipx khác hoặc là file pipx không quản lý (ví dụ `jg` của npm cùng prefix) thì bỏ qua bước CLI và báo cần gỡ gì; chỉ dùng `pipx install --force` để làm mới chính venv `ai-skill-jev`, cài mới thì không force. Skill kiểm tra identity/version qua `ai doctor --json` và không chạy executable không nhận diện được để gửi dữ liệu.

### 5.2 Command surface của MVP

Chỉ có ba lệnh cho người dùng, **sẽ triển khai**, chưa tồn tại trong repo:

```text
ai auth            # lần đầu: chọn provider, nhập key ẩn, consent, validate, thiết lập jg (5.3)
                   # đã có cấu hình: menu đổi key/provider
ai logout          # xóa key, bỏ consent; `jg` vẫn đi qua shim và trả no_key (5.4)
ai doctor [--json] # trạng thái, không network; --json để skill đọc
```

- `jg` (shim, mục 6.5) không phải lệnh cho người dùng: đó là console script để skill `jevgrep` và các skill `ai-*` gọi Jev.
- `ai doctor`:
  - Chỉ đọc metadata và chạy `jg --version`. Không network, không mở keyring hay dialog unlock.
  - Hiện: enabled/disabled, provider, model, host của endpoint, secret backend, ngày consent, `jg: shim|shadowed|missing` (kèm version `jg` để tham khảo, không dùng để chặn), cách sửa nếu có. Không hiện key, kể cả vài ký tự cuối.
  - `--json`: cùng nội dung kèm identity (`tool: ai-skill`, version) để skill nhận diện. Kết quả là hint, không bảo đảm key giải mã được tại lúc dùng.
- Agent không bao giờ tự chạy `ai auth` hay `ai logout`; chỉ chạy `ai doctor --json` và `jg`.
- Các lệnh của bản trước được gộp hoặc hoãn:
  - `ai auth status`, `ai jev status` → `ai doctor`.
  - `ai auth logout` → `ai logout`.
  - `ai jev exclude` → bỏ; phạm vi cài skill đã quyết định repo nào dùng (6.4).
  - `ai jev search` → skill gọi thẳng shim `jg` (8.1).
  - Auth không tương tác (`--stdin --accept-data-policy`) → hoãn sau MVP; CI không có key thì chạy luồng cũ.
- Hoãn sau MVP: auth không tương tác; engine native (Phụ lục A).

### 5.3 Wizard

Đã có cấu hình active thì `ai auth` hiện menu: (1) đổi key hoặc provider → chạy lại các bước dưới; (2) thoát. Không có mục loại trừ repo (6.4). Lần đầu thì vào thẳng các bước:

1. Chọn TypeSafe (đề xuất cho cấu hình mới), Vercel AI Gateway, OpenRouter, OpenCode Zen, Custom endpoint. Không có mục Skip: thoát (Ctrl+C) thì giữ nguyên trạng thái, chưa auth nghĩa là chạy workflow hiện tại.
2. Hiện host của endpoint, model và chi phí. Không mặc định chọn gateway chỉ vì upstream chọn như vậy.
3. Với Custom:
   - Nhập HTTPS base URL và model.
   - URL không được có userinfo, query hay fragment; không đi theo redirect.
   - HTTP chỉ chấp nhận cho loopback literal `localhost`/`127.0.0.1`, khi user xác nhận rõ là local proxy.
4. Kiểm tra secure backend có sẵn **trước** khi hỏi key. Không có backend an toàn thì dừng, giữ nguyên trạng thái và hướng dẫn mở keyring; không fallback sang file plaintext.
5. Nhập key bằng hidden TTY input. Nếu runtime không bảo đảm được nhập ẩn: dừng phần auth và hướng dẫn chạy trong terminal riêng. Không dán key vào chat.
6. **Consent, một lần duy nhất.**
   - Hiển thị: host nhận dữ liệu; những gì được gửi (đoạn source từ các repo có skill theo phạm vi cài, nơi agent chạy discovery, trừ nhóm file loại trừ ở 6.4); giá tại ngày cài; ZDR chỉ áp cho enterprise; phạm vi áp dụng gồm cả skill `jevgrep` (lệnh `jg`); cách tắt (`ai logout`); muốn tránh một repo thì cài skill theo project ở các repo cần dùng, hoặc `ai logout`.
   - User phải xác nhận rõ ràng. Từ chối thì không bật, cấu hình cũ giữ nguyên.
7. **Validation bắt buộc.**
   - Gửi một request Noul tổng hợp: không chứa source, có `criteria`, dùng đúng provider/endpoint/model mà engine sẽ dùng. Chi phí không đáng kể.
   - Thất bại thì không bật, cấu hình cũ giữ nguyên, chỉ báo mã lỗi an toàn.
   - Không có chế độ bỏ qua validation. Bản đầu vừa ghi "có thể chọn" vừa ghi "không bật nếu chưa validate"; bản này chốt là bắt buộc.
8. Lưu secret vào OS store, sau đó ghi metadata (kèm consent) atomically. In trạng thái đã lưu, không in key.
9. Kiểm tra `jg`: nếu thiếu hoặc khác version được hỗ trợ, in lệnh cài đã pin. Trạng thái khi đó là `enabled, engine unavailable`, và luồng cũ chạy cho tới khi user tự cài.
10. Thiết lập `jevgrep`, tự động, không hỏi: kiểm tra `jg` trên PATH có phải shim không. Nếu `jg` của npm che shim, đưa thư mục bin của pipx lên trước trong PATH của user (chỉ biến PATH cấp user; lưu giá trị cũ để `ai logout` khôi phục; in thay đổi đã làm). Không sửa được (ví dụ npm nằm trong PATH cấp máy) thì in cách sửa thủ công và trạng thái `jevgrep: shadowed`. Thành công thì in `jevgrep: ready`; shell đang mở cần mở lại để nhận PATH mới.

### 5.4 Semantics của chưa auth, cancel, logout

- **Chưa auth:** mặc định disabled. Không có config là trạng thái hợp lệ và chính là trạng thái "không dùng Jev"; không có lệnh `skip` riêng. Các skill không bao giờ hỏi auth.
- **Ctrl+C/cancel:** không đổi cấu hình trước đó; cài mới thì vẫn disabled. Cancel không tắt cấu hình đang active; muốn tắt thì `logout`.
- **Logout:**
  - Chuyển sang disabled trước, sau đó xóa secret thuộc công cụ này và bỏ reference.
  - Bỏ consent. PATH cấp user giữ nguyên như `ai auth` đã sửa: `jg` vẫn trỏ vào shim, shim trả `no_key` và không chạy `jg` thật, nên credentials cũ của `jg auth` (nếu có) cũng không gửi được source. Không lưu `path_backup` nữa (`jev.json` cũ có field này thì logout bỏ đi). Muốn dùng lại thì chạy `ai auth` và nhập key lại.
  - Nếu OS store lỗi: trạng thái vẫn là disabled; báo rõ secret chưa xóa được, không tuyên bố logout sạch.
- **Đổi key hoặc provider:**
  - Validate cái mới trước, consent lại nếu provider/endpoint đổi, lưu reference mới, commit metadata, rồi mới xóa secret cũ.
  - Reference cũ được ghi vào `pending_secret_delete` ngay trong lần commit metadata đó (write-ahead) và chỉ bỏ khỏi danh sách sau khi xóa xong. Ctrl+C hay crash giữa hai bước vẫn để lại dấu vết, nên `ai logout` xóa được key cũ.
  - Failure không được để metadata trỏ tới key không tồn tại.
  - Key/provider cũ không bao giờ được dùng làm fallback.
- **Không tự nhập key từ nguồn khác:** `.env`, `TYPESAFE_API_KEY`, `TYPESAFE_AI_API_KEY`, cấu hình `jg`, worktree, shell history. CI chạy luồng cũ (auth không tương tác hoãn sau MVP). Mục đích là không để biến môi trường còn sót lại bật Jev khi user chưa auth hoặc đã logout.

## 6. Lưu trữ, mã hóa và consent

### 6.1 Secret store

Dùng dependency `keyring` (pin version), chỉ trong CLI tùy chọn. Khởi tạo trực tiếp class backend trong allowlist (ví dụ `keyring.backends.Windows.WinVaultKeyring`) thay vì `keyring.get_keyring()`. Nhờ vậy `PYTHON_KEYRING_BACKEND`, `keyringrc.cfg` hay plugin từ project không tin cậy không đổi được backend.

| Nền tảng | Backend | Khi không dùng được |
| --- | --- | --- |
| Windows | Windows Credential Manager (DPAPI, gắn với logon user) | Luồng cũ; không ghi key ra JSON |
| macOS | Keychain | Auth interactive có thể xin unlock; discovery chạy nền không được treo chờ dialog |
| Linux desktop | Secret Service hoặc KWallet đã cấu hình an toàn | Thiếu session bus hoặc keyring bị khóa: luồng cũ |
| Headless/container/WSL/remote | Chỉ dùng khi thực sự có secure backend tương thích | Không tự chuyển sang `keyrings.alt`, plaintext, null hay backend tùy ý |

Nguồn: [keyring documentation](https://keyring.readthedocs.io/en/latest/), [Microsoft DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata). Hệ điều hành chịu trách nhiệm bảo vệ key at-rest; ứng dụng không giữ master key cạnh ciphertext và không viết một implementation DPAPI song song.

Service name riêng là `ai-skill/jev`, account reference sinh ngẫu nhiên. Secret gắn với provider/endpoint/model của profile và được kiểm tra khớp trước khi dùng, để metadata bị sửa không thể khiến key bị gửi sang host khác.

**Threat model:**
- Bảo vệ key khi lưu trên đĩa, và tránh rò rỉ qua artifact.
- Không hứa bảo vệ trước malware, admin, hay tiến trình cùng user có quyền đọc secret store.
- Key phải tồn tại trong memory khi gọi API, và nằm trong file tạm trong suốt thời gian `jg` chạy (6.2).
- Python không bảo đảm zeroization; giảm thời gian giữ reference, không chủ động log request header hay tạo core dump.

### 6.2 Credentials tạm cho `jg`

- **Vị trí:**
  - Windows: `%LOCALAPPDATA%\ai-skill\jg-run\<random>\`.
  - POSIX: `$XDG_RUNTIME_DIR/ai-skill/jg-run/<random>/` nếu biến này tồn tại (tmpfs, thuộc user); nếu không thì `${XDG_CACHE_HOME:-~/.cache}/ai-skill/jg-run/<random>/`.
  - Không bao giờ đặt trong repo, worktree, thư mục mạng hay `%TEMP%` dùng chung.
- **Quyền:**
  - POSIX: dir `0700`, file `0600`, tạo bằng exclusive create.
  - Windows: kiểm tra ACL kế thừa từ profile chỉ cấp cho user hiện tại, SYSTEM và Administrators. Sai thì dùng luồng cũ. Không coi `chmod(0600)` là đủ.
  - Chống symlink hoặc reparse point bất ngờ tại nơi ghi.
- **Nội dung:** đúng schema `credentials.json` của version `jg` đã pin, ví dụ `{"provider":"typesafe","apiKey":"…"}`. P0.5 xác minh schema cho từng provider, kể cả custom.
- **Env của tiến trình con:**
  - Copy env hiện tại, đặt `XDG_CONFIG_HOME=<dir>` (đường dẫn tuyệt đối).
  - Xóa `TYPESAFE_API_KEY` và `TYPESAFE_AI_API_KEY`.
  - Key không bao giờ nằm trong argv hay env.
- **Dọn dẹp:**
  - Xóa thư mục trong `finally`.
  - Timeout hoặc cancel: kill cả cây tiến trình rồi mới xóa. Trên Windows dùng `taskkill /F /T`, vì `jg` là shim `.cmd` có tiến trình Node con (giống cách `sessions.py` đang làm); trên POSIX dùng process group.
  - Mỗi lần `ai` khởi động: quét và xóa các thư mục `jg-run/*` mồ côi.
- **Giới hạn nói thẳng:** trên SSD hoặc filesystem có journaling, xóa file không bảo đảm xóa vật lý. Crash hay mất điện để lại file cho tới lần chạy kế tiếp. Đây là đánh đổi user xác nhận tại gate P0.5; nếu không chấp nhận thì dùng Phụ lục A.

### 6.3 Metadata

- Windows: config ở `%APPDATA%\ai-skill\jev.json`.
- POSIX: `${XDG_CONFIG_HOME:-~/.config}/ai-skill/jev.json`; chỉ chấp nhận XDG path tuyệt đối.
- Các field: `schema_version`, `enabled`, `provider`, `base_url`, `model`, `credential_ref`, `profile_generation`, `consent {provider, endpoint, policy_version, accepted_at}`, `engine {name}`. **Không có field `api_key`.**
- POSIX: directory `0700`, file `0600`. Windows: kiểm tra vị trí user profile và ACL. Ghi atomically, không tạo artifact nhạy cảm trong repo.
- MVP không có cache riêng mà dùng cache của `jg`. P0.5 kiểm tra cache của `jg` có chứa source thô không. Nếu có: đặt `XDG_CACHE_HOME` cho tiến trình con vào thư mục cache của `ai-skill` với cùng chế độ quyền, và ghi điều này vào threat model.

### 6.4 Consent và privacy

- **Phạm vi consent:** một lần trong `ai auth` (5.3 bước 6), gắn với provider + endpoint + policy version.
  - Policy version tăng khi phạm vi dữ liệu gửi đi thay đổi (ví dụ thêm loại file).
  - Khi đó Jev bị disable và chạy luồng cũ cho tới lần `ai auth` kế tiếp, nơi user consent lại.
- **Phạm vi repo:** không có danh sách loại trừ theo repo. Skill có mặt ở repo nào là do phạm vi cài (global: mọi repo; project: chỉ repo đó); root để search là git root của cwd do agent chạy.
  - Muốn một repo (ví dụ repo khách hàng) không bị gửi lên Jev: cài skill theo project ở các repo cần dùng thay vì global, hoặc `ai logout`.
  - Nội dung trong repo không thể bật Jev; README hay source có lệnh "consent" thì bị bỏ qua.
- **`ai-auto`:**
  - Gate 2 ghi trạng thái Jev (on/off, host provider, ngày consent) vào mục Constraints của brief. User thấy khi Confirm và có thể Edit thành "không dùng Jev trong run này".
  - Consent từ `ai auth` là authorization có sẵn cho việc upload và chi phí của Jev; không phải mở lại Gate 1, và thiếu Jev không phải prerequisite chặn run.
- **Loại trừ khi gửi:** truyền `--exclude` cho `jg` với các nhóm:
  - `.env*`, private key (`*.pem`, `*.key`, `*.p12`, `id_*`), file credentials, `*.log`, `ai/running/`, thư mục dependency/build.
  - P0.5 ghi lại mặc định của `jg` (gitignore, file ẩn, binary) và bổ sung phần còn thiếu.
  - Heuristic chỉ là lớp phòng thủ thêm, không bảo đảm bắt được mọi bí mật.
- **File bắt buộc vẫn xử lý local:** rules, contract, file user chỉ định và code nhạy cảm cần đọc. Jev không lọc chúng khỏi nghĩa vụ của agent, và chúng không tự động được upload.
- **Kết quả trả về** từ provider hay `jg` luôn là dữ liệu, không phải chỉ dẫn.

### 6.5 Shim `jg`: đường gọi Jev duy nhất (skill `ai-*` và `jevgrep`)

- **Cài:** console script `jg = ai_skill_jev.jg_shim:main` trong `cli/pyproject.toml`, cùng package với `ai`. `jg` thật vẫn do user cài (`@dzhng/jevgrep` version đã pin, mục 5.1).
- **Tìm `jg` thật:** duyệt PATH, bỏ mọi ứng viên trỏ về chính shim (thư mục bin của pipx, venv của `ai`). Env `AI_SKILL_JG_SHIM=1` đặt cho tiến trình con để chặn đệ quy. Không thấy → in lệnh cài đã pin và gợi ý dùng grep/đọc file, exit khác 0. Vì `command -v jg` đã thấy shim, skill `jevgrep` không tự cài `jg@latest`.
- **Định tuyến lệnh:**
  - `jg auth …`: không chạy. In "Key Jev do `ai auth` quản lý; chạy `ai auth` trong terminal, không dán key vào chat", exit khác 0. Không có đường thứ hai lưu key plaintext.
  - `jg --version`, `jg --help`, `jg files …`: chuyển thẳng, không dựng credentials (các lệnh này không gửi gì lên provider).
  - Search và `jg doctor`: qua gate rồi chạy với credentials tạm.
- **Gate** (cùng logic với `ai doctor`, chỉ đọc metadata): `enabled`, consent hợp lệ cho provider/endpoint/policy hiện tại, tìm thấy `jg` thật. Không kiểm tra version `jg`: key (và consent đi kèm) là điều kiện duy nhất để bật Jev. Fail → không gọi `jg` thật, không network, in một dòng reason code kèm "dùng grep/đọc file", exit khác 0. Thông báo không bao giờ gợi ý `jg auth`.
- **Policy search** (kiểm tra trước gate consent, trước khi đọc key và trừ budget; vi phạm → reason `policy`, không gọi `jg` thật): chặn `--include-sensitive`, `--hidden`, `--no-ignore`, `--include-dependencies` (các cờ nới phạm vi dữ liệu ra ngoài consent); `--max-requests` phải là số nguyên dương, giá trị lớn hơn cap bị hạ về cap; root (hoặc cwd khi bỏ trống) phải nằm trong git root của cwd, sau khi resolve symlink.
- **Chạy:** dùng lại `jg_runner` (6.2): credentials tạm, env sạch, chèn `--max-requests <cap>` nếu lệnh chưa có, deadline, kill cả cây tiến trình, dọn mồ côi, scrub chuỗi key trong output. Không cắt output: skill `jevgrep` cần nguyên khối tới `End context.`. Exit code của `jg` thật được giữ nguyên.
- **Theo trạng thái `ai auth`, không có cấu hình riêng:**
  - Chưa từng chạy `ai auth` hoặc đã `logout` (không có key active): không có key Jev thì không dùng `jg`. Shim trả `ai-skill jev: unavailable (reason=no_key)`, exit 3, cho mọi lệnh (search, `doctor`, `--help`, `files`, …) mà không chạy `jg` thật, không đọc keyring; `jg auth` bị chặn với hướng dẫn chạy `ai auth`. Người gọi chạy workflow như cũ; ai-skill không đọc credentials của `jg auth` (JEV-12).
  - Đã auth có key: định tuyến và gate như trên; `jg auth` bị chặn.
- **Bị che trên PATH:** nếu `jg` của npm đứng trước shim, skill `jevgrep` gọi thẳng `jg` thật và sẽ đòi `jg auth`. `ai auth` tự sửa thứ tự PATH cấp user (5.3 bước 10); `ai doctor` báo `jevgrep: ready|shadowed|jg missing|not configured` và in cách sửa nếu cần. Kiểm tra trong PowerShell, cmd và Git Bash vì PATH của các shell có thể khác nhau.
- **Budget:** có cờ `--ai-run-dir <run-dir>` (skill `ai-*` truyền; shim bỏ cờ này khỏi argv trước khi gọi `jg` thật) thì tính vào budget của run (8.1, 9.2). Không có cờ (skill `jevgrep`, lane Direct) thì chỉ áp trần `--max-requests` cho từng lần gọi; lời gọi qua `jevgrep` không tính vào budget của coordinator (mục 15).

## 7. Provider

| Provider (ID trong `jg`) | Base URL | Model mặc định của `jg` 0.8.5 |
| --- | --- | --- |
| `typesafe` | `https://api.typesafe.ai/v1` | `jev-1.13.0` (docs đã có `jev-1.14.0`) |
| `vercel` | `https://ai-gateway.vercel.sh/typesafe/v1` | `typesafe-ai/jev` |
| `openrouter` | `https://openrouter.ai/api/v1` | `typesafe/jev-1.13` |
| `opencode` | `https://opencode.ai/zen/v1` | `jev-1.13` |
| `custom` | User cung cấp | User cung cấp |

- Mọi provider đều nhận `POST <base>/systemone` với cùng schema native.
- P0.5 xác minh `jg` có cho chọn model với preset hay không.
  - Nếu không: model là cái `jg` quyết định, và metadata ghi đúng model đó. Không quảng bá là chạy 1.14 khi thực tế là 1.13.
  - Chất lượng đa ngôn ngữ có thể khác giữa 1.13 và 1.14, nên benchmark phải ghi model thực tế.
- Chưa gọi live gateway nào trong nghiên cứu. Validation trong `ai auth` là smoke test bắt buộc; preset chưa qua smoke không được quảng bá là đã kiểm chứng.
  - Gateway trả 422 vì thiếu `criteria.true` thì không dùng được với engine `jg`.
  - Không tự đổi model để né lỗi access.
- HTTP của chính CLI trong MVP **chỉ dùng cho validation**:
  - Stdlib, bật TLS verification, chặn redirect, timeout 15 s, giới hạn kích thước response, không retry.
  - Request là Noul có `criteria`.
  - Validator kiểm tra: tập key của `answers` khớp tập key của `questions`, `type` khớp, `noul` là số hữu hạn trong [0,1]. Thiếu `usage` thì ghi `unknown`, không ghi 0.
  - Chỉ log mã HTTP và error class an toàn; bỏ raw error body vì provider có thể echo lại key.

## 8. Discovery: luồng và giới hạn

### 8.1 Luồng thực thi (engine `jg`)

1. **Local gate.** `ai doctor --json` cho biết disabled, chưa consent, `jg` không phải shim (thiếu, bị che), hoặc CLI không nhận diện được → đi luồng cũ ngay và không nạp `references/jev.md`.
2. **Khi nào dùng.** Chỉ cho câu hỏi về hành vi ("how/where/why") cần quét nhiều file, tức đúng chỗ `ai-main` hiện gọi scout `t1`. Tra exact path/symbol, vài file nhỏ, hoặc context đã đủ → grep/read/LSP. Không gọi model để quyết định có nên gọi model.
3. **Gọi search.** Agent chạy `jg "<query>" <root>` (shim, 6.5); trong run orchestrated thêm `--ai-run-dir <run-dir>`. Shim:
   - Kiểm tra gate: enabled, consent, version.
   - Có `--ai-run-dir`: kiểm tra và tăng counter trong `<run-dir>/jev-budget.json` (có file lock) **trước** khi gọi; hết budget → reason code `budget`, 0 network.
   - Dựng credentials tạm (6.2).
   - Chạy `jg` thật với `--max-requests <cap>`, `--exclude` các nhóm ở 6.4, deadline tổng.
   - Dọn dẹp, rồi scrub output: nếu có chuỗi key thì hủy output.
4. **Output.** Output nguyên văn của `jg`: tóm tắt, danh sách file xếp hạng, excerpt. Shim không cắt output. Thiếu marker `End context.` hoặc agent chỉ đọc được một phần → `coverage=partial`.
5. **Tiếp tục source tracing.** Agent coi output là reading leads, rồi mở source, caller, test thật để kiểm chứng. Thiếu context, timeout hay lỗi → scout cũ hoàn thiện.

### 8.2 Chính sách giữ context

- Không lọc bỏ: task text, project rules, contract đã xác nhận, file user chỉ định, exact match bắt buộc, security gate, test bắt buộc.
- Thứ hạng của Jev chỉ để ưu tiên thứ tự đọc, không dùng để kết luận "file không liên quan".
- Không kết luận "không có implementation" chỉ vì zero hit. Luôn trả coverage và đường tiếp tục.
- Không gửi toàn bộ `run.md`, transcript hay diff tới Jev chỉ để chọn lane, tier, retry hay kết luận arbiter. Với ba lane hiện tại, luật xác định rẻ và rõ hơn một API call.

### 8.3 Budget khởi điểm (hiệu chỉnh bằng số đo ở P0.5)

| Bound | Giá trị khởi điểm | Ghi chú |
| --- | --- | --- |
| `--max-requests` mỗi search | 100 (mặc định của `jg` là 1000) | Đặt lại bằng p95 đo được ở spike × 1,5, làm tròn |
| Số search mỗi run (coordinator) | ≤ 3 | Shim đếm trong `<run-dir>/jev-budget.json` qua `--ai-run-dir`; coordinator chép vào `run.md`; không reset khi resume |
| Deadline mỗi search | 90 s tổng, gồm cả thời gian khởi động Node | Đặt lại theo p95 ở spike. Bản đầu dùng 5 s/request, chưa hiệu chỉnh, và có thể khiến gần như mọi lời gọi rơi về fallback |
| stdout chuyển cho agent | Shim không cắt (skill `jevgrep` cần nguyên khối) | `references/jev.md` hướng dẫn đọc output lớn theo từng phần; đọc không hết thì `coverage=partial` |
| Concurrency | 1 search tại một thời điểm cho mỗi coordinator | P0.5 kiểm tra `jg` có flag giới hạn concurrency nội bộ (mặc định 32) hay không |

Retry 429/529 do `jg` xử lý (theo `retry-after`, thiếu header thì chờ 1 s) và nằm trong deadline tổng. Wrapper không retry lại cả search. Các số trên là **giá trị khởi điểm để đo**, không phải yêu cầu hiệu năng đã được xác nhận.

## 9. Run state và fallback

### 9.1 Cache

Dùng cache của `jg`: namespace gồm provider/endpoint/model/version, TTL 7 ngày, tối đa 256 MiB. MVP không thêm SQLite, Redis, vector DB hay embedding service. Cache hit không thay thế việc agent đọc source hiện tại.

### 9.2 Ai sở hữu request và ngân sách

- Trong run orchestrated, coordinator là caller duy nhất của `jg`.
  - Worker nhận excerpt và path qua brief; không gọi auth hay search, không nhận key.
  - Giới hạn đã biết: phần lớn token của run orchestrated tiêu ở worker, nên MVP không tối ưu phần đó. P4 đo riêng; mở cho worker là việc sau MVP.
- Shim ghi counter (không chứa bí mật) vào `<run-dir>/jev-budget.json` **trước** khi gọi `jg` thật; coordinator chép số liệu vào `run.md`. Resume không reset ngân sách đã tiêu; search chưa rõ đã kết thúc hay chưa vẫn tính là đã dùng.
- Trạng thái: `off`, `ready`, `used`, `fallback`.
- Reason code: `no_cli`, `no_key`, `no_consent`, `engine_missing`, `locked_store`, `auth_error`, `provider_error`, `budget`, `timeout`, `incomplete`.
- Lane Direct không có run-dir: budget tính theo từng lần gọi, không tạo cả orchestration run chỉ để dùng Jev.

### 9.3 Bảng xử lý lỗi

| Điều kiện | Xử lý |
| --- | --- |
| Chưa auth hoặc đã logout, thiếu CLI hoặc dependency bảo mật | Luồng cũ ngay: không network, không install, không hỏi key |
| Có key nhưng chưa consent hoặc consent lỗi thời | Luồng cũ, không upload |
| `jg` hoặc Node thiếu | Luồng cũ với `engine_missing`; in lệnh cài một lần, không tự cài |
| Keyring bị khóa, hỏng, không truy cập được | Timeout phần tùy chọn, đi luồng cũ; không bật popup khi chạy nền |
| 401, 403 (403 để phòng; docs không liệt kê), model unavailable | Không retry, không đổi provider; disable Jev cho cả run, báo gọn một lần |
| 429/529 | `jg` tự xử lý trong deadline; hết deadline thì luồng cũ |
| Hết deadline tổng | Kill cả cây tiến trình, dọn credentials tạm, đi luồng cũ |
| Exit code ≠ 0, dòng cuối output không phải footer `End context.` (marker nằm trong excerpt không tính), hoặc jg báo `Output truncated at the byte limit` | Dùng phần evidence hợp lệ nếu có, `coverage=partial`, hoàn thiện bằng luồng cũ; thiếu không có nghĩa là không tồn tại |
| Output chứa chuỗi key | Không chuyển cho agent, hủy output, ghi `provider_error` |
| Hết budget | Dừng gọi Jev cho run, luồng cũ tiếp tục; không đổi phạm vi task |
| User cancel toàn tác vụ | Kill, dọn dẹp, hủy tác vụ theo ý user; không dùng fallback để phớt lờ lệnh cancel |

Fallback chỉ thay **công cụ tối ưu**, không che lỗi của build, test hay arbiter. Lỗi verification chính vẫn được báo và xử lý như trước.

## 10. Tích hợp vào các skill

| File | Thay đổi dự kiến | Giữ nguyên |
| --- | --- | --- |
| `skills/ai-main/SKILL.md` | Một pointer ngắn tới discovery tùy chọn, đặt ở chỗ scout `t1`; availability gate chạy trước khi nạp reference | Resume, scope lock, quyết định lane |
| `skills/ai-auto/SKILL.md` | Gate 2 ghi trạng thái Jev vào Constraints; thiếu Jev không phải prerequisite chặn run | Confirm/hash, Done means, Authorized effects, hỏi lại khi scope đổi |
| `skills/ai-orchestrate/SKILL.md` | Coordinator gọi search khi hữu ích; ghi counter, usage, fallback vào `run.md`; final report có dòng usage của Jev | Ownership, deps, risk, tier, checks, arbiter độc lập |
| `skills/ai-orchestrate/references/worker-brief.md` | Mục `Discovery context` (tuỳ chọn): path/range/excerpt, coverage, fallback. Lệnh cấm nằm trong `Rules` bắt buộc, có trong mọi brief kể cả khi không có lead: worker không chạy `jg`, không auth, không nhận key, không tự cài tool hay chạy `jg auth`, kể cả khi skill `jevgrep` upstream có mặt | Worker vẫn đọc đủ source và chứng minh checks |
| `skills/ai-orchestrate/references/jev.md` — mới | Policy cho run orchestrated: khi nào dùng, budget, consent, fallback. Không phụ thuộc skill `jevgrep` (có thể không được cài) | Không lặp hướng dẫn dài trong ba file SKILL.md |
| `skills/jevgrep/` | **Không còn trong repo** (đã gỡ). Cài cùng ai-skill từ upstream qua installer (mục 10.1); không vendor, không patch, không đồng bộ. Dùng key của `ai auth` qua shim `jg` (mục 6.5) | — |
| `skills-lock.json`, `.gitignore` — mới | Không commit `skills-lock.json`. `.gitignore` chặn `skills-lock.json`, `.agents/skills/`, `.claude/skills/`: nếu người bảo trì cài jevgrep ở scope project trong checkout này rồi lỡ commit, `npx skills add 0x01001/ai-skill` sẽ phát hiện và cài kèm nó | — |
| `README.md` | Cài CLI bằng `pipx` từ git; ba lệnh `ai auth`, `ai logout`, `ai doctor`; không có loại trừ theo repo (chọn phạm vi cài: global hay theo project); không có `skip` (chưa auth là chạy như cũ); consent; key at-rest và credentials tạm; yêu cầu Node/`jg`; ghi chú bộ ai-skill không dùng credentials của `jg auth`; lệnh cài chính là installer (kèm `jevgrep`, có `--no-jev`) và hai lệnh `npx skills add` tương đương; cập nhật bằng `npx skills update`, gỡ `jevgrep` bằng `npx skills remove jevgrep`; `ai auth` dùng chung cho `jevgrep` qua shim `jg`, không cần `jg auth`; `ai auth` tự đặt bin của pipx trước bin global của npm trong PATH; không cài CLI `ai` hoặc chưa chạy `ai auth` thì `jevgrep` cần `jg auth` riêng như upstream; giới hạn của số liệu | Lệnh cài ba skill hiện tại vẫn đủ cho luồng cũ |
| `package.json`, `bin/install.mjs`, `bin/lib.mjs`, `test/install.test.mjs` — mới (đã làm) | Installer một lệnh: gọi `skills add` hai lần qua `npm exec` (không qua shell nên `*` không bị glob), chuyển tiếp cờ, `--no-jev`, từ chối `--skill`, dừng nếu lượt ai-skill lỗi; rồi cài CLI `ai` (pipx, tự cài pipx nếu thiếu, `ensurepath`) và `jg@latest` (npm, Node ≥ 22), lỗi chỉ cảnh báo; `--no-jev` bỏ phần Jev; in bước tiếp theo `ai auth`. Test: `npm test` với lệnh giả, không cài thật | Không có dependency npm; không đụng discovery của `npx skills` |

Mọi lời gọi Jev của ai-skill đi qua shim `jg`, và chỉ sau khi `ai doctor --json` báo `jg: shim`. Các skill `ai-*` không chạy `jg auth`, không yêu cầu skill `jevgrep`, không gọi `jg` khi gate fail (khi đó `jg` có thể là bản thật dùng credentials của `jg auth`). Luồng worker và cross-runtime không kế thừa chỉ dẫn kiểu "luôn chạy jg".

### 10.1 `jevgrep`: không ở trong repo, cài cùng ai-skill từ upstream

- Cài: mặc định qua installer `npx -y github:0x01001/ai-skill` (mục 5); thủ công là `npx skills add dzhng/jevgrep --skill jevgrep` (thêm `-g` cho mọi project). Lock của người dùng ghi nguồn `dzhng/jevgrep`. Đã thử cài ở scope project ngày 2026-11-14: lock có 3 entry nguồn `0x01001/ai-skill` và 1 entry nguồn `dzhng/jevgrep`; `npx skills check` lấy từ 2 nguồn.
- Cập nhật: `npx skills update` (hoặc `npx skills update jevgrep`). Lệnh này kéo bản mới nhất của upstream, cùng lúc với ba skill `ai-*` từ `0x01001/ai-skill`. `npx skills check` cho biết có bản mới hay không.
- Gỡ: `npx skills remove jevgrep`. Đây là cách tắt hẳn `jevgrep`; `ai logout` tắt Jev: shim trả `no_key` và không chạy `jg` (mục 6.5). `ai logout` không khôi phục PATH, nên `jg` vẫn đi qua shim sau logout và skill `jevgrep` cũng nhận `no_key`. Gỡ hẳn: `pipx uninstall ai-skill-jev`; khi đó `jg` về bản npm.
- Repo ai-skill không có bản copy, script đồng bộ hay GitHub Actions nào cho `jevgrep`; không có việc bảo trì định kỳ.
- Nội dung skill là bản upstream nguyên văn. Có CLI `ai` và đã chạy `ai auth`: `jg` là shim, nên skill không tự cài `jg@latest` và không cần `jg auth`; `ai auth`, consent, `logout` áp dụng cho nó. Không có CLI `ai`: skill tự cài `jg@latest`, nhắc `jg auth`, key plaintext trong `%APPDATA%\jevgrep`, nằm ngoài kiểm soát của ai-skill.
- Engine `jg` thật không bị pin: shim chạy bất kỳ bản nào đang cài (upstream skill cài `@latest`), một bản phát hành mới không bao giờ làm Jev tự tắt. Các chặn trong shim (flag mở rộng phạm vi, `--max-requests`, root trong git repo) không phụ thuộc version. Nếu bản `jg` mới đổi format credentials hay cờ, search lỗi sẽ rơi về fallback (`auth_error`/`provider_error`) và luồng cũ chạy tiếp.

## 11. File layout của MVP

Các file mới đề xuất:

```text
cli/
  pyproject.toml        # console script ai và jg (shim), dependency keyring (pin version)
  src/ai_skill_jev/
    __init__.py
    cli.py              # argparse: auth (kèm menu đổi key/provider), logout, doctor
    credentials.py      # backend allowlist, metadata, consent, rotation/logout
    provider.py         # bảng preset, request validation và validator
    jg_runner.py        # credentials tạm, env sạch, cap, deadline, kill cây tiến trình, dọn mồ côi, scrub output
    jg_shim.py          # shim jg, đường gọi Jev duy nhất: tìm jg thật, định tuyến, gate, budget run, gọi jg_runner (mục 6.5)
  tests/
    test_auth.py
    test_credentials.py
    test_jg_runner.py
    test_jg_shim.py
    test_fallback.py
skills/ai-orchestrate/references/jev.md
.gitignore                         # skills-lock.json, .agents/skills/, .claude/skills/
package.json, bin/install.mjs      # installer một lệnh (đã làm, mục 10)
report/<yyyy-MM-dd>-jev-spike.md   # kết quả P0.5
```

MVP không có: SQLite cache, `discovery.py`, lệnh `search`/`status`/`exclude` riêng, plugin registry, abstract provider hierarchy, scheduler, embedding DB, fork Jevgrep. Chỉ khi gate P0.5 chọn engine dự phòng thì thêm theo Phụ lục A.

## 12. Kế hoạch triển khai theo thứ tự

### P0 — Baseline và dọn repo

- Ghi lại các scenario đại diện: Direct, ai-auto, orchestration, resume, global install, thiếu key.
- jevgrep:
  - `skills/jevgrep/` và `skills-lock.json` đã không còn trong checkout (2026-11-14).
  - Thêm `.gitignore` (mục 10); không commit `skills-lock.json`.
  - Installer `package.json` + `bin/install.mjs` và mục Install của README (đã làm ngày 2026-11-14).
- Chốt schema của `ai doctor --json` và các reason code.
- Không sửa các lỗi không liên quan trong report trước, trừ khi chúng chặn feature này.
- **Done:**
  - Baseline chạy không cần Jev.
  - `npx skills add <checkout> --list` chỉ liệt kê ba skill `ai-*` (JEV-13).
  - Chạy installer vào project thử: 3 + 1 skill, lock ghi đúng hai nguồn, `npx skills check` chạy đúng (JEV-14).
  - Sau khi push: `npx -y github:0x01001/ai-skill --list` chạy được từ GitHub.

### P0.5 — Spike đo token, trước mọi code CLI

Mục tiêu: trả lời câu hỏi "Jev có giảm token của model chính trên task discovery của bộ skill này không" với chi phí nhỏ.

- **Chuẩn bị:**
  - User tự cài `@dzhng/jevgrep@latest` (ghi lại version đã đo), rồi chạy `jg auth` trong terminal riêng với `XDG_CONFIG_HOME=<thư mục sandbox tuyệt đối>`. Key không đi qua chat.
  - Xóa thư mục sandbox khi spike xong.
  - Dùng key có hạn mức chi phí nếu provider hỗ trợ.
- **Bộ case:** 8–10 case discovery trên repo thật: tiếng Anh và tiếng Việt, câu hỏi hành vi đa file, và 2 case exact lookup làm đối chứng. Mỗi case có danh sách file/range cần tìm, do người gán nhãn **trước** khi chạy.
- **Hai nhánh, cùng model/effort:**
  - A: luồng cũ (scout `t1` khi cần).
  - C: coordinator dùng `jg` thay scout.
  - Mỗi nhánh chạy một lần; đủ để quyết định, không đủ để công bố.
- **Đo:**
  - Token của model chính (input, cache read/write, output) và token của scout, lấy từ `sessions.py usage` hoặc usage của `Agent`.
  - Số request, token và chi phí của Jev.
  - Latency p50/p95.
  - Recall trên các file đã gán nhãn.
- **Ghi lại cho P1–P2:**
  - `jg --help`: các flag concurrency, exclude, max-requests, chọn model.
  - Schema `credentials.json` của từng provider.
  - `jg` có in usage hay không.
  - Cache của `jg` có chứa source thô không.
  - Mặc định ignore/hidden/binary.
  - Gateway nào chạy được `/systemone` với key của user.
- **Kết quả:** `report/<yyyy-MM-dd>-jev-spike.md`, kèm số liệu thô.
- **Gate, do user quyết định:**
  - **Đi tiếp** khi, trên nhóm discovery đa file: median input token của model chính ở C giảm ≥ 15% so với A, recall không thấp hơn, và tổng chi phí (model + Jev) không cao hơn A.
  - **Không đạt:** dừng feature, không viết CLI; giữ plan và report làm hồ sơ.
  - **Đạt:** user chọn engine:
    - `jg` + credentials tạm (đề xuất); hoặc
    - native reranker (Phụ lục A). Khi đó phải dựng prototype và đo lại nhánh này trước P2.

### P1 — CLI và auth

- Package, entry point `ai`; wizard auth có consent và validation bắt buộc.
- `ai logout`, `ai doctor [--json]`; menu đổi key/provider trong `ai auth`.
- Keyring theo allowlist, metadata ghi atomically, rotation, ràng buộc endpoint.
- **Done:**
  - Nhập ẩn chạy được trên terminal thật.
  - Chưa auth, đã logout hoặc không key thì không có network.
  - Key không rơi vào artifact nào.
  - Lỗi secret store không dẫn tới fallback plaintext.
  - Kiểm tra Windows trước, rồi macOS/Linux trên môi trường tương ứng; platform nào chưa chạy thì ghi "chưa verified".

### P2 — Shim `jg` (engine)

- `jg_runner`: kiểm tra version, credentials tạm, env sạch, cap, deadline, kill cây tiến trình, dọn mồ côi, scrub output, tính coverage.
- `jg_shim` (mục 6.5): tìm `jg` thật, chặn đệ quy, chặn `jg auth`, gate, budget run qua `--ai-run-dir`, fallback `no_key` (không chạy `jg`) khi chưa có key. Bước 10 của `ai auth`: phát hiện và sửa thứ tự PATH cấp user, khôi phục khi `logout`.
- **Done:**
  - Fault injection gồm: `jg` thiếu, `jg` bản mới hơn (vẫn chạy), 401, 429, timeout, crash giữa chừng, output thiếu marker, output chứa sentinel.
  - Mọi trường hợp đều fallback hợp lệ, sentinel scan sạch, không vượt budget.
  - JEV-15 pass: skill `jevgrep` search được với `%APPDATA%\jevgrep` trống; logout/chưa consent/chưa từng auth đúng hành vi; không cần lệnh nào ngoài `ai auth`; trên Windows `command -v jg` (Git Bash), `Get-Command jg` (PowerShell), `where jg` (cmd) đều trả shim.

### P3 — Tích hợp skill và README

- Cập nhật các file ở mục 10. Gate chỉ nạp reference khi Jev thực sự khả dụng và phù hợp.
- **Done:**
  - Chạy end-to-end ba tình huống no-key (kể cả sau `logout`), có key và đã consent, lỗi, ở cả Direct và orchestration.
  - Luồng cũ không phát sinh auth prompt hay cài dependency.

### P4 — Benchmark phát hành

- Chạy benchmark mục 14. Không tuyên bố nhanh hơn hay rẻ hơn chỉ dựa trên microbenchmark.
- Ghi README như changelog của feature, kèm raw usage.
- **Done:** đạt release gates ở 14.3. Chỉ bật đường tối ưu cho nhóm tác vụ có evidence lợi ích; nhóm còn lại bypass bằng rule.

Thứ tự phụ thuộc: P0 → P0.5 → (gate) → P1 → P2 → P3 → P4. Dataset của P0.5 được tái sử dụng ở P4. Một integration owner giữ contract của CLI và chỉnh các skill, để không có hai đường auth khác nhau.

### Sau MVP (chỉ khi P4 có dữ liệu ủng hộ)

- Cho worker gọi search, dùng bộ đếm budget chung trong run dir (có file lock).
- Auth không tương tác cho CI.
- Marker trong repo để opt-out (chỉ có thể tắt, không bật được).
- Engine native và cache SQLite nếu đổi engine.

## 13. Ma trận kiểm thử nghiệm thu

| Nhóm | Scenario bắt buộc | Kỳ vọng |
| --- | --- | --- |
| Legacy | Không CLI, không config, không keyring, không key, không Node | Luồng cũ hoàn thành; 0 request Jev; 0 prompt auth hay install |
| Logout | `ai logout` khi đang có run chạy, logout rồi resume, logout khi store lỗi | Trạng thái như chưa auth giữ qua process và run; 0 request; PATH cấp user giữ nguyên, `jg` vẫn trỏ vào shim và trả `no_key` (cả khi đã có credentials của `jg auth`) |
| Lệnh | `ai auth` lần đầu và khi đã có cấu hình (đổi key/provider); `ai logout`; `ai doctor` và `ai doctor --json` | Chỉ có ba lệnh; `doctor` không network, không mở keyring, `--json` đúng schema |
| Auth | Nhập ẩn, key rỗng/CRLF/quá dài, cancel, provider sai, validation thất bại, từ chối consent | Không echo key, không có secret trong argv; profile trước đó nguyên vẹn |
| Storage | Store bị khóa hoặc thiếu, metadata hỏng, ghi bị ngắt giữa chừng, rotation/logout lỗi | Không plaintext lâu dài; không trỏ sai key; chạy nền không treo chờ unlock |
| Credentials tạm | Chạy bình thường, timeout (kill cây tiến trình), crash rồi lần chạy kế tiếp, ACL Windows sai | Sentinel scan sạch; file mồ côi được dọn; ACL sai thì đi luồng cũ |
| Consent | Chưa consent, đổi endpoint, policy version tăng, symlink escape, file thuộc nhóm loại trừ | 0 upload ngoài consent; key không đi qua brief hay worker |
| Engine | Không có `jg`, `jg` bản mới, không có Node, có sẵn credentials của `jg auth` | Thiếu `jg` → luồng cũ với đúng reason code; `jg` bản mới → vẫn search bằng key của `ai auth`; không bao giờ dùng credentials của `jg auth` |
| Network | 401/403/429/529, DNS/TLS lỗi, timeout | `jg` retry trong deadline hoặc fallback; không đổi provider |
| Output | Thiếu footer `End context.` ở dòng cuối, marker chỉ nằm trong excerpt, output bị jg cắt theo byte limit, output lớn, exit code ≠ 0, chứa sentinel | Đặt coverage partial hoặc hủy output; diagnostics không chứa secret hay source |
| Retrieval | VN/EN, exact symbol, code nằm sâu, đa file, snippet gây hiểu nhầm | Có evidence kèm semantics partial; thiếu không bị coi là không tồn tại; mở rộng bằng luồng cũ khi cần |
| Security | Prompt injection trong code, `.env`, heuristic credential bỏ sót, output sinh ra | Score không biến thành chỉ dẫn; loại trừ + consent vẫn là gate thật |
| Runtime | CLI cài local/global, thư mục có dấu cách, worktree, resume ở Direct và orchestration | Đường dẫn đúng; budget không reset; không gọi Jev lặp lại theo từng worker |
| Packaging | Cài CLI bằng pipx từ git vào môi trường sạch; chỉ cài skill mà không có CLI; `npx skills add <checkout> --list` | `ai auth` chạy thật khi có CLI; luồng cũ không phụ thuộc CLI; chỉ có ba skill `ai-*` |
| Shim `jg` | Chưa từng auth; đã auth; logout; chưa consent; `jg auth`, `jg files`, `jg --version`; `jg` thật thiếu, `jg` thật bản mới hoặc `--version` lỗi (vẫn search); npm `jg` che shim (PATH cấp user và cấp máy); shim gọi lại chính nó; timeout giữa chừng; `--ai-run-dir` hết budget, resume | Search dùng key của `ai auth`, không đụng `%APPDATA%\jevgrep`; gate fail thì 0 request và thông báo không nhắc `jg auth`; `jg auth` không chạy; bị che ở PATH cấp user thì `ai auth` tự sửa, `logout` khôi phục; cấp máy thì `ai doctor` báo `shadowed`; không đệ quy; sentinel scan sạch |
| jevgrep upstream | Cài bằng installer có và không có `--no-jev`; đã cài ở scope project và global; `npx skills update` sau khi upstream đổi; run orchestrated khi có `jevgrep` | `ai-*` không đổi hành vi và không gây prompt `jg auth`; `jevgrep` lên bản mới nhất của upstream; worker không tự cài tool hay chạy auth |

Dùng stdlib `unittest` cho các contract có nhánh rẽ hoặc rủi ro. Fake HTTP service, fake `jg` và config tạm chỉ dùng trong test để chứng minh fallback, không phải implementation giả. Kiểm tra secure storage thật được làm riêng, với sentinel secret do test tạo và xóa; không dùng key cá nhân để chụp log. Integration với key thật chỉ chạy khi user đã cho phép rõ về chi phí và dữ liệu.

## 14. Benchmark token, latency và chất lượng

### 14.1 Thiết kế so sánh

Các nhánh dùng cùng task, base commit, runtime, model, effort và tool budget:

- **A — Legacy:** workflow hiện tại, không có Jev.
- **C — Jev:** coordinator gọi `jg` qua shim, chạy riêng cold cache và warm cache.
- **B — Local-only:** chỉ cần khi dùng engine native (Phụ lục A). B là đối chứng cho prefilter tự viết, để không gán lợi ích của grep/dedupe cho Jev.

Quy mô:
- 12 case: 4 dùng để hiệu chỉnh (có thể lấy từ spike) và 8 holdout. Chia theo tiếng Anh/tiếng Việt và theo nhóm: exact lookup, discovery đa file, bug fix, review, resume/fallback, security-sensitive.
- Chạy 2 lần mỗi nhánh; báo giá trị thô, median và p95; không tuyên bố ý nghĩa thống kê từ tập nhỏ.
- Ước tính khoảng 12 × (A + C cold + C warm) × 2 = 72 lần chạy agent. Ghi chi phí dự kiến và xin user đồng ý trước khi chạy.

Security và fallback có test xác định riêng, không nghiệm thu dựa trên điểm benchmark mang tính xác suất.

### 14.2 Metrics

- Task success theo tests/checks và review độc lập đã có trong contract.
- Token của model chính (input, output, cache read, cache write); số lần gọi scout/tool và số retry.
- Số request Jev, usage thực tế theo provider/model (model thực, không phải alias), cache hit/miss, reason của timeout/fallback.
- Số byte và số excerpt đưa vào model chính; recall các file/range cần thiết trên bộ đã gán nhãn.
- Wall time end-to-end và thời gian discovery, p50/p95, tách cold/warm.
- Tổng chi phí = model chính + Jev/gateway + retry/fallback, tính theo giá provider tại ngày đo. Thiếu usage hoặc giá thì ghi `unknown`, không suy ra 0.

Công thức: `token_saved_ratio = 1 - main_tokens_C/main_tokens_A`; `total_cost_saved_ratio = 1 - total_cost_C/total_cost_A`. Luôn báo kèm số task pass. Không dùng `token_saved_ratio` để khẳng định chi phí giảm nếu Jev tiêu nhiều hơn phần tiết kiệm được.

### 14.3 Release gates

**Bắt buộc:**
- Toàn bộ acceptance về no-key, logout, privacy, credentials tạm và fallback phải pass.
- Không bỏ mandatory context hay gate nào.
- Không có regression task success đã biết so với baseline trên holdout.
- Bất kỳ lỗi an toàn nào cũng chặn release, dù token trung bình tốt hơn.

**Mục tiêu tối ưu để quyết định rollout (chưa phải kết quả):** trên nhóm discovery phù hợp:
- giảm ít nhất 15% median input token của model chính so với A;
- tổng chi phí của C thấp hơn A (và không cao hơn B nếu có nhánh B);
- p95 toàn tác vụ không tăng quá 10%.

Không đạt thì giữ feature ở dạng opt-in nhưng bypass nhóm đó bằng rule, và công bố đúng số liệu. User có thể điều chỉnh mục tiêu **trước** benchmark; không chỉnh sau khi đã nhìn holdout.

## 15. Rủi ro, quyết định chưa có dữ liệu và ngoài phạm vi

- **Mức tiết kiệm chưa biết.** Scout `t1` vốn đã che chắn context của model chính, nên lợi ích có thể nhỏ. Gate P0.5 quyết định trước khi đầu tư.
- **Credentials tạm.** Có plaintext ngắn hạn trên đĩa (6.2). User chấp nhận rõ tại gate P0.5; nếu không thì dùng Phụ lục A.
- **`jg` thay đổi theo version.** Schema `credentials.json`, flag hay format output có thể đổi. Không pin (user chốt: cập nhật là việc của upstream); bản mới không làm Jev tự tắt. Nếu bản mới làm hỏng tích hợp, shim fallback (`engine_missing`, `provider_error`, `timeout`) và skill đi luồng cũ; sửa shim khi phát hiện.
- **Node ≥ 22, Python ≥ 3.9.** Là dependency của engine và CLI. Thiếu thì installer chỉ cảnh báo và đi luồng cũ, không phải lỗi cài đặt.
- **Installer sửa môi trường người dùng.** Mặc định nó chạy `pip install --user pipx` (khi thiếu), `pipx ensurepath` (thêm bin của pipx vào PATH user) và `npm install --global @dzhng/jevgrep@latest` (nâng `jg` global hiện có lên bản mới nhất). README nói rõ; ai không muốn thì dùng `--no-jev`.
- **`jg` quét nhiều tầng, gửi nhiều nội dung hơn một reranker.** Consent phải nói rõ điều này; `--max-requests` là trần.
- **Tương thích gateway.** Gateway bắt buộc `criteria` sẽ trả 422 với `jg`; validation và spike phải phát hiện được.
- **Worker không được hưởng lợi trong MVP.** Mở rộng sau khi có dữ liệu.
- **Tiếng Việt.** Cần corpus riêng; preset của `jg` đang dùng 1.13, trong khi 1.14 ghi cải thiện đa ngôn ngữ. Không thêm bước dịch bằng LLM vì dễ triệt tiêu lợi ích token.
- **Provider alias/version.** Gateway có thể đổi mapping; smoke test và model identity là điều kiện trước khi đưa ra tuyên bố về provider.
- **Keyring trên headless/WSL** thường không có Secret Service, nên đi luồng cũ là kết quả bình thường.
- **Secret heuristic** không thay được consent hay ranh giới root; không tuyên bố source đã "sạch bí mật".
- **`jevgrep` upstream được cài mặc định; chỉ nằm trong guard của ai-skill khi có CLI `ai` và đã chạy `ai auth` (mục 6.5).** Người không cài CLI `ai` vẫn có skill này; khi được kích hoạt nó tự cài `jg@latest`, nhắc `jg auth`, lưu key plaintext; consent và `logout` của ai-skill không áp dụng cho nó. Điều này lệch với yêu cầu gốc "không key thì chạy như cũ" ở phạm vi cả session (các skill `ai-*` vẫn chạy như cũ). README nói rõ; ai không dùng Jev thì cài với `--no-jev` hoặc `npx skills remove jevgrep`. Installer giờ cài CLI mặc định, nhưng trước `ai auth` thì `pipx ensurepath` chỉ nối bin của pipx vào cuối PATH, nên `jg` vẫn trỏ tới bản npm và `jevgrep` vẫn hành xử như upstream cho tới khi chạy `ai auth`.
- **Hai đường credentials song song** chỉ còn khi không có CLI `ai`, khi chưa từng chạy `ai auth` hoặc đã `logout`, hoặc khi `jg` của npm che shim mà `ai auth` không sửa được (PATH cấp máy). Khi đó `jg` có thể gửi code lên Jev bằng credentials của `jg auth`, ngoài consent của `ai auth`. `ai doctor` báo `jevgrep: shadowed`; README nói rõ.
- **`ai auth` sửa biến PATH cấp user.** Đây là thay đổi ngoài repo; chỉ đổi thứ tự (đưa thư mục bin của pipx, nơi có `ai`, lên trước npm) và in ra. `logout` cố ý giữ nguyên để `jg` vẫn đi qua shim; `pipx uninstall ai-skill-jev` gỡ shim thì `jg` tự về bản npm. Shell đang mở phải mở lại.
- **Shim phụ thuộc thứ tự PATH và cách agent gọi lệnh.** Shell khác nhau (PowerShell, cmd, Git Bash) có PATH khác nhau; agent gọi `jg` bằng đường dẫn tuyệt đối sẽ bỏ qua shim. Phát hiện bằng `ai doctor` (báo `jg: shadowed`), không chặn được tuyệt đối.
- **Skill `ai-*` gọi `jg` theo tên.** Nếu `jg` trong shell của agent là bản thật, lời gọi có thể dùng credentials của `jg auth` và bỏ qua consent, budget, `--exclude` nhóm file. Giảm nhẹ: chỉ gọi khi `ai doctor --json` (chạy trong cùng shell) báo `jg: shim`; JEV-12 kiểm tra.
- **Không có loại trừ theo repo.** Phạm vi cài chỉ quyết định skill có mặt ở đâu; key của `ai auth` dùng cho cả máy. Nếu `jevgrep` cài global, hoặc người dùng tự gọi `jg` ở repo khác, code repo đó vẫn có thể bị gửi. Giảm nhẹ: consent ghi rõ điều này; README hướng dẫn cài theo project cho máy có repo nhạy cảm, hoặc `ai logout`. Đánh đổi user chấp nhận ngày 2026-11-14 để `ai auth` không phải hỏi repo-root.
- **Lời gọi qua `jevgrep` không nằm trong budget của coordinator.** Shim chỉ áp `--max-requests` cho từng lần gọi.
- **Upstream đổi hành vi bất kỳ lúc nào.** `npx skills update` kéo thẳng bản mới, không qua review của ai-skill. Đây là đánh đổi đã chọn để luôn có bản mới nhất.
- **Description của `jevgrep` bảo subagent dùng `jg`.** Trong run orchestrated, worker có thể chạy `jg` ngoài budget của coordinator. Mục `Rules` bắt buộc của mọi worker brief ghi rõ discovery Jev do coordinator làm, worker không chạy `jg` và worker không tự cài tool hay chạy auth, nhưng không bảo đảm tuyệt đối vì skill upstream nằm ngoài kiểm soát.

Không triển khai trong feature này:
- Thay model writer/arbiter bằng Jev.
- Tự phê duyệt deploy hay push.
- Tự chọn tier theo score.
- Fork hoặc vendor Jevgrep, kể cả file skill.
- Vector DB, fine-tuning.
- Failover qua nhiều provider.
- Vault master password tự viết.
- Đồng bộ key qua worker hoặc remote host.

## 16. Điều kiện hoàn thành plan và bước tiếp theo

Plan bao phủ:
- Jev tùy chọn, lệnh `ai auth` đúng tên, chọn provider.
- Secure storage thay cho plaintext lâu dài, consent một lần.
- Tương thích no-key: không key hoặc chưa chạy `ai auth` thì chạy như cũ. Bỏ `ai auth skip` vì trùng với trạng thái chưa auth (user quyết định ngày 2026-11-14).
- Tích hợp vào skill hiện có; `jevgrep` được installer cài kèm thẳng từ upstream (bỏ được bằng `--no-jev`), cập nhật bằng `npx skills update`.
- Budget, cache, fallback.
- Spike đo token trước khi xây, test và benchmark toàn tác vụ.

Khi triển khai: P0 → P0.5 → gate do user quyết định. Không yêu cầu user gửi key trong chat.

Xác minh trong các phiên lập và review plan:
- Đã đọc bốn URL user cung cấp, tài liệu API chính thức, và source `auth.ts`, `storage.ts`, `providers.ts`, `cache.ts`, `evaluator.ts`, các `package.json` của Jevgrep tại commit đã ghi, cùng nội dung skill local.
- Bản review đã đối chiếu lại 17 khẳng định với nguồn và sửa các dữ kiện sai.
- Đã kiểm tra cơ chế cài của `npx skills` (docs và source `vercel-labs/skills`) và của plugin Claude Code (docs) cho câu hỏi cài kèm jevgrep (Phụ lục B). Đã chạy thử `skills` 1.8.1: `add` bỏ qua nguồn thứ hai; installer cài đủ 4 skill ở scope project trong thư mục tạm. Chưa chạy qua `github:` vì installer chưa được push.
- Không cài package, không chạy `ai auth`/`jg auth`, không thu thập API key, không gọi API trả phí, không sửa code hay skill.

## Phụ lục A — Engine dự phòng: native reranker

Chỉ dùng khi gate P0.5 chọn. Giữ nguyên phần chung (mục 5–6, trừ 6.2); thay `jg_runner.py` bằng `jev.py` (transport), `discovery.py` và `cache.py`. Benchmark khi đó phải có nhánh B.

- **Luồng:**
  1. Local gate.
  2. Bypass tác vụ đã rõ.
  3. Lấy candidate từ `git ls-files -co --exclude-standard` cộng grep quanh match/heading, trong root do coordinator giới hạn. Không scan hay upload toàn repo mặc định.
  4. Kiểm tra eligibility và loại trừ, rồi dedupe theo content hash chính xác.
  5. Batch Noul:
     - `state` gồm câu hỏi của user và scope tối thiểu.
     - Mỗi question có key là ID do client đặt; `instructions` chứa path/range/text trực tiếp; **luôn có `criteria`**.
     - Prompt tiếng Anh ngắn, giữ nguyên source và query tiếng Việt.
  6. Trả về path, range, score, content hash và excerpt nguyên văn.
  7. Agent đọc tiếp bằng công cụ hiện có.
- **Budget (đã sửa để nhất quán):**

  | Bound | Giá trị |
  | --- | --- |
  | Candidate mỗi discovery | ≤ 48 |
  | Kích thước mỗi candidate | ≤ 4 KiB, cắt ở ranh giới dòng |
  | Kích thước mỗi request | ≤ 48 KiB JSON (khoảng 25% giới hạn 64k token) |
  | Request mỗi discovery | ≤ 4 |
  | Request mỗi run | ≤ 12, kể cả retry |
  | Bypass (không gọi Jev) | ≤ 8 đoạn và ≤ 8 KiB tổng |

  - Tổng 4 request × 48 KiB = 192 KiB, đủ chứa 48 candidate × 4 KiB.
  - Candidate vượt budget thì không gửi: trả về dạng `unranked` và đặt `coverage=partial`.
  - Deadline lấy theo p95 đo được trên prototype, không đặt cứng 5 s.
- **Retry:**
  - 429/529: tối đa 1 lần. Backoff theo `Retry-After` nếu có; không có thì exponential ngắn (1 s + jitter). Phải nằm trong deadline và budget.
  - Timeout, DNS/TLS, redirect: không retry.
  - 400/413/422 hay schema sai: không retry, không cache, đi luồng cũ.
- **Validator:** như mục 7. Thiếu answer, trùng key, `NaN` hoặc giá trị ngoài [0,1] thì loại cả batch.
- **Cache SQLite (stdlib):**
  - Key là hash từ schema/prompt/policy version + provider + endpoint đã chuẩn hóa + model + profile generation + repo identity + query/scope + path/range/content hash của candidate. Không hash API key.
  - Chỉ cache score đã qua validate; TTL 4 ngày, tối đa 10.000 entry.
  - Không cache timeout hay lỗi. Cache hit vẫn kiểm tra lại consent và eligibility của file hiện tại.

## Phụ lục B — Cài kèm `jevgrep` khi cài ai-skill: các cơ chế đã kiểm tra

Câu hỏi: cài `0x01001/ai-skill` có tự kéo skill `jevgrep` từ `dzhng/jevgrep` theo không, để repo không phải giữ bản copy?

Nguồn: README, `src/plugin-manifest.ts`, `src/blob.ts`, `src/git.ts` của `vercel-labs/skills` (nhánh `main`, đọc ngày 2026-11-14); tài liệu Claude Code về plugin marketplace và plugin dependencies.

| Cơ chế | Tự cài skill từ repo khác? | Chi tiết | Kết luận |
| --- | --- | --- | --- |
| Frontmatter `SKILL.md` (`npx skills add`) | Không | Không có field khai báo phụ thuộc | — |
| `.claude-plugin/marketplace.json` khi cài bằng `npx skills` | Không | `plugin-manifest.ts` chỉ nhận source local dạng `./...`; source remote bị bỏ qua | — |
| `skills-lock.json` | Không | Là lock của project phía người cài. `npx skills experimental_install` khôi phục lock của project hiện tại, không đọc lock của repo nguồn | — |
| Git submodule `skills/jevgrep` → `dzhng/jevgrep` | Không đáng tin | Đường clone có `git submodule update --init --recursive --depth 1`, nhưng lỗi chỉ cảnh báo. Khi không pin ref, CLI có thể đi đường blob qua skills.sh (`tryBlobInstall`); đường này dựng cây từ GitHub API nên bỏ sót skill trong submodule. Ngoài ra submodule kéo cả repo upstream, skill nằm lồng ở `skills/jevgrep/skills/jevgrep`, và nội dung vẫn là bản nguyên văn | Không dùng |
| Mảng `skills` trong `package.json` + `npx skills experimental_sync` | Chỉ trong project npm | Experimental, chỉ áp dụng khi người dùng phân phối qua npm package | Không dùng |
| Plugin marketplace của Claude Code: `dependencies` + source `git-subdir` | **Có** | Plugin `ai-skill` khai báo `dependencies: ["jevgrep"]`; entry `jevgrep` dùng `git-subdir` (url `dzhng/jevgrep`, path `skills/jevgrep`, pin `sha`) thì Claude Code tự cài kèm. Nhưng: chỉ Claude Code; đổi lệnh cài sang `/plugin marketplace add` + `/plugin install`; tên skill bị namespace theo plugin; và dependency luôn được cài, nên `jevgrep` không còn là tùy chọn | Không dùng: chỉ Claude Code, không có cách bỏ `jevgrep` khi cài |
| Installer riêng `npx -y github:0x01001/ai-skill` (`package.json` + `bin/install.mjs`) | **Có** | Chạy `skills add` cho từng nguồn, chuyển tiếp cờ, `--no-jev` để bỏ. Mọi agent mà `npx skills` hỗ trợ; lock ghi nguồn gốc của từng skill | **Chọn** |

Kết luận:
- `npx skills update` cập nhật mỗi skill từ đúng nguồn ghi trong lock lúc cài. Bản copy đi kèm trong repo chỉ mới khi repo ai-skill được cập nhật, nên `skills/jevgrep` không ở trong repo nữa. Chỉ installer vừa cài `jevgrep` cùng ai-skill, vừa để `update` kéo bản mới nhất của `dzhng/jevgrep`.
- User chọn (2026-11-14): không vendor, không GitHub Actions. Installer `npx -y github:0x01001/ai-skill` chạy `skills add` cho hai nguồn; sau đó một lệnh `npx skills update` cập nhật cả ba skill `ai-*` và `jevgrep`, mỗi skill lên bản mới nhất của nguồn tương ứng.
- Đánh đổi: `jevgrep` là bản upstream nguyên văn, nằm ngoài guard của ai-skill (mục 15).
