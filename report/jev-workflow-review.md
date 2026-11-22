# Review workflow Jev hiện tại

**Kết luận: Request Changes.** Kiến trúc đúng hướng: giữ `jg` để tìm lead, giữ `ai decide` là tùy chọn, và để coordinator/arbiter chịu trách nhiệm xác minh kết quả. Chưa nên coi `jg` là an toàn cho repo có dữ liệu không được gửi ra ngoài; chưa nên bật `ai decide` tự động.

**Phạm vi:** ba skill `ai-main`, `ai-auto`, `ai-orchestrate`, cùng CLI, shim, installer và bộ chấm eval liên quan. Review implementation hiện tại trên `2d7ff36` và phần thay đổi cục bộ liên quan; không phải review lại bản plan. Không sửa implementation.

## Cần sửa trước khi mở rộng sử dụng

### 1. [blocking] Exclusion của `jg` thay đổi theo thư mục gốc tìm kiếm

**Vị trí:** [`jg_runner.py:140–151`](../cli/src/ai_skill_jev/jg_runner.py#L140), [`jg_runner.py:199–207`](../cli/src/ai_skill_jev/jg_runner.py#L199).

Shim cho phép chọn thư mục con trong repo, nhưng luôn truyền cùng các pattern loại trừ. Engine `@dzhng/jevgrep` 0.8.5 đang cài áp pattern theo đường dẫn tương đối với **search root**, không phải Git root. Với các exclusion phụ thuộc thư mục cha như `ai/running/`, thay đổi search root có thể khiến brief hoặc report trở thành dữ liệu ứng viên gửi sang provider. Đây là sai lệch so với cam kết loại trừ, không chỉ là giới hạn của regex dò secret. Chưa quan sát thấy dữ liệu thực tế bị gửi.

**Đề xuất:** kiểm exclusion theo Git root trước khi gọi engine. Nếu chưa cần tìm trong thư mục con, chỉ cho phép Git root của checkout/worktree; nếu cần, chuyển pattern theo search root và từ chối root nằm trong vùng loại trừ. Kiểm thử offline bằng engine/fixture tương thích để xác nhận cùng một file bị loại trừ ở mọi search root được phép.

### 2. [blocking trước khi bật `ai decide` tự động] Eval có thể PASS khi không quyết định được mẫu nào

**Vị trí:** [`eval.py:140–176`](../cli/src/ai_skill_jev/eval.py#L140), [`eval.py:228–238`](../cli/src/ai_skill_jev/eval.py#L228), [`decisions.py:1349–1359`](../cli/src/ai_skill_jev/decisions.py#L1349).

Đã xác nhận offline: 30 mẫu `lane` có nhãn quyết định, cả 30 kết quả giả lập là `abstain`, scorer trả `decided: 0`, `passed: true`. Khi `decided` bằng 0, tỷ lệ sai được tính là 0; CLI eval vẫn có thể in mục đề nghị thêm vào `EVALUATED_KINDS`. Một bộ chấm như vậy không chứng minh được model đủ hữu ích để tự động can thiệp workflow.

**Trạng thái hiện tại:** bảng `EVALUATED_KINDS` rỗng, repo không có bộ mẫu hay kết quả eval đã commit. `ai doctor --json` có thể báo `decide: ready` nhưng `kinds: []`; không có kind nào được phép gọi tự động. Đây là mặc định an toàn, không phải lỗi cài đặt.

**Đề xuất:** yêu cầu coverage tối thiểu trên nhóm mẫu cần quyết định, tách riêng nhóm kỳ vọng `abstain`, giữ ngưỡng 0 lỗi theo hướng không an toàn. Chốt ngưỡng trước khi đo; chỉ đăng ký từng kind sau khi bộ mẫu người gán nhãn đạt trên đúng provider/model. Mock chỉ kiểm tra logic, không đo chất lượng phân loại.

## Các lỗi quan trọng

### 3. [important] Call site `leads` không tuân theo gate độc lập

**Vị trí:** [`ai-orchestrate/SKILL.md:60`](../skills/ai-orchestrate/SKILL.md#L60), [`jev.md:18–27`](../skills/ai-orchestrate/references/jev.md#L18), [`jev.md:127`](../skills/ai-orchestrate/references/jev.md#L127).

Gate chung yêu cầu `leads` có trong `kinds`; bước dispatch lại yêu cầu gọi `ai decide leads` sau mọi lần search khi chỉ gate `jg` bật. Trạng thái `jg` sẵn sàng còn `kinds: []` là đường chạy bình thường hiện nay. Làm theo câu lệnh ở call site sẽ gọi một kind chưa eval; bỏ qua thì tài liệu không chỉ rõ giao lead cho worker thế nào.

**Đề xuất:** tách rõ hai nhánh. `jg` chỉ tạo danh sách để đọc; nếu `leads` không được phép hoặc lỗi, coordinator tự đối chiếu source và chỉ chuyển các lead đã kiểm tra. Thêm kịch bản offline `jg: ready`, `kinds: []`: vẫn dùng được discovery, không gọi System One.

### 4. [important] `auth_error` từ engine không luôn đến được coordinator

**Vị trí:** [`jg_runner.py:326–344`](../cli/src/ai_skill_jev/jg_runner.py#L326), [`jev.md:136`](../skills/ai-orchestrate/references/jev.md#L136).

Shim chỉ phân loại lỗi dựa vào stderr khi engine thoát khác 0 **và** stdout trống. Đối chiếu engine 0.8.5: lỗi ở lớp CLI được ghi ra stdout; lỗi xác thực còn có thể xuất hiện trong phần trạng thái của kết quả partial. Hai trường hợp này không đi qua nhánh `auth_error`, nên workflow có thể tiếp tục gọi Jev thay vì tắt nó cho run.

**Đề xuất:** phân biệt kết quả, lỗi xác thực và lỗi chung dựa trên trạng thái có cấu trúc của engine. Khi engine chỉ trả lỗi chung thì fallback `provider_error`, không suy diễn từ chữ trong đoạn source. Dùng fixture offline mô phỏng đúng stdout và status của engine thật.

### 5. [important] Installer có thể gỡ `jg` đang chạy được trước khi có bản thay thế

**Vị trí:** [`bin/lib.mjs:186–198`](../bin/lib.mjs#L186), [`bin/lib.mjs:211–226`](../bin/lib.mjs#L211), [`bin/lib.mjs:305–314`](../bin/lib.mjs#L305).

Khi npm và pipx dùng chung bin dir, nhánh xử lý xung đột có thể gỡ `jg` global trước khi cài pipx package **và** private engine. Nếu một trong hai bước cài lỗi, nhánh khôi phục shim ở cuối không phục hồi lần gỡ sớm. Một bước tùy chọn có thể làm mất công cụ vốn đang hoạt động.

**Đề xuất:** chuẩn bị private engine trước; chỉ thay launcher sau khi bản thay thế sẵn sàng, và giữ hoặc phục hồi bản cũ nếu công bố shim thất bại. Thêm case chung bin dir cho lỗi ở mỗi bước cài.

### 6. [important, chưa ảnh hưởng auto khi `kinds: []`] Kết quả model và nhánh fallback có thể làm mất luật cứng

| Vị trí | Sai lệch | Sửa tối thiểu |
| --- | --- | --- |
| [`decisions.py:473–479`](../cli/src/ai_skill_jev/decisions.py#L473), [`decisions.py:519–542`](../cli/src/ai_skill_jev/decisions.py#L519) | `jobs` có quyền ghi nhưng không chạm vùng nhạy cảm không có risk floor; model có thể trả `R0` (read-only) và kéo tier xuống. | Floor theo quyền ghi: `R0` nếu không ghi, `R1` nếu ghi, `R2` cho vùng nhạy cảm. |
| [`decisions.py:1264–1270`](../cli/src/ai_skill_jev/decisions.py#L1264), [`decisions.py:1309–1315`](../cli/src/ai_skill_jev/decisions.py#L1309) | `guard` nhận ra secret khi redact, nhưng nếu block vượt giới hạn chunk thì trả `flagged: true` với `categories: []`. Mất `secret_or_exfil`, nên ai-auto không nhận được tín hiệu dừng và hỏi. | Giữ category đã xác định cục bộ trong kết quả trả sớm. |
| [`decisions.py:734–746`](../cli/src/ai_skill_jev/decisions.py#L734) | Khi cả contract và finding bị cắt, lần abstain đầu tiên bỏ qua nhánh gắn `safety: true` dù finding cũng bị cắt. | Tính safety và scope suy được từ `written_files` trước khi trả abstain. |

## Điều nên giữ và thứ tự triển khai

- Giữ hai gate độc lập và fallback không cần Jev. Kết quả `jg` chỉ là đầu mối, không phải chứng cứ. Job chỉ `done` sau khi coordinator đối chiếu diff/check; arbiter vẫn độc lập.
- Ưu tiên sửa ranh giới dữ liệu, cách nhận diện lỗi và fallback `leads` cho `jg`. Cho tới khi đó, dùng grep/scout cục bộ cho repo không được gửi code hoặc nội dung run ra ngoài.
- Sau đó sửa luật cứng và bộ chấm eval. Bật từng kind; ưu tiên loại chỉ bổ sung tín hiệu cho coordinator. Giữ quyết định bỏ prerequisite, thay scope và phân luồng ở workflow cục bộ cho tới khi có bằng chứng chúng không hạ mức thận trọng. Đo thời gian và lượng context so với grep/scout thay vì mặc định mỗi lần gọi Jev đều tiết kiệm.

**Xác minh đã làm:** đọc luồng và đối chiếu source với engine 0.8.5 đang cài; kiểm tra registry và các artifact eval được Git theo dõi; chạy đúng một kiểm tra offline cho scorer với kết quả giả lập (`30` mẫu, `0` quyết định, vẫn `PASS`). Không gọi Jev thật, không chạy bộ test đầy đủ, không kiểm tra CI từ xa.

## Đối chiếu 9 đề xuất dùng Jev để cải thiện skill

**Nguyên tắc:** LLM đề xuất và giải thích; Jev trả tín hiệu cho một câu hỏi hẹp; workflow giữ quyền quyết định và thực thi theo scope đã xác nhận. Quyền, giới hạn dữ liệu và điều kiện kiểm chứng không phụ thuộc vào điểm của model. Đây là hướng sử dụng trong [tài liệu TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one.md), không phải cam kết rằng thêm một lượt gọi sẽ nhanh hoặc rẻ hơn.

| # | Đề xuất | Đối chiếu với repo và khuyến nghị |
|---|---|---|
| 1 | Điều phối model — `Choice` | Đã có `jobs` phân loại công việc để suy ra tier/effort. `lane` chọn Direct/Orchestrate/Auto, **không** chọn model. Thử ghi nhận đề xuất của `jobs` mà chưa đổi dispatch; giữ [tier theo rủi ro, pin, cap và ceiling](../skills/ai-orchestrate/references/models.md). Đo cả chi phí làm lại; chọn rẻ hơn một lượt chưa chắc rẻ hơn cả run. |
| 2 | Hàng rào an toàn — `Noul` | Đã có `guard` cho nội dung ngoài repo và luật cục bộ cho rủi ro/approval. `0.03` chỉ là xác suất ước lượng cho câu hỏi cụ thể, không phải chứng nhận an toàn. Dùng tín hiệu để tăng thận trọng: đánh dấu, tóm tắt thay vì chép nguyên văn, hoặc dừng hỏi. `flagged: false`, abstain và lỗi đều không biến dữ liệu ngoài thành chỉ thị đáng tin và không cấp thêm quyền. Kiểm soát dữ liệu gửi đi **trước** khi gọi model. |
| 3 | Kiểm soát gọi tool — `Choice` | Không thêm một cuộc gọi Jev trước mỗi tool. Giữ allow/deny/ask ở lớp thực thi hoặc sandbox, dựa trên tool, đối số thực tế và quyền đã cấp. Ví dụ `npm publish` cần approval nếu chưa được phép, bất kể Jev trả `0.62` hay `0.99`. Tín hiệu bổ sung có thể đề nghị người dùng kiểm tra, không thể cho lệnh vượt chính sách. |
| 4 | Phân loại hộp thư — `Choice` | Chưa có inbox chung. Gần nhất là `failure` phân loại lỗi và `duplicate_issue` gợi ý trùng issue, nhưng không nên dùng chúng thay inbox. Chỉ cân nhắc khi thực sự có hàng đợi lớn: phân luồng read-only trước, giữ mục chưa rõ trong hàng chờ, không tự archive hoặc trả lời. Đo số mục cần xử lý ngay bị xếp nhầm. |
| 5 | Xếp hạng lại — `Noul` hoặc `Score` | `jg` đã trả danh sách xếp hạng, còn [`leads`](../cli/src/ai_skill_jev/decisions.py#L645) đã xếp hạng lại bằng `Score`. Tái dùng thay vì thêm một reranker. Sửa fallback theo từng lead trước, rồi so sánh grep/scout, `jg` và `jg` + `leads` trên cùng câu hỏi: số vị trí đúng trong top-k, đoạn quan trọng bị bỏ, và lượng context nạp. Không mặc định 30 → 2; nếu rút ngắn làm mất bằng chứng thì giữ nhiều hơn hoặc dùng scout. [Ví dụ TypeSafe dùng Noul với BM25 và dữ liệu pháp lý](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md), không chứng minh lợi ích của lượt xếp hạng thứ hai trong repo này. |
| 6 | Đánh giá đầu ra LLM — `Score` hoặc rubric nhỏ | Đã có `evidence`, `claims`, `check_output` để rà từng dấu hiệu cụ thể. Ưu tiên exit code, test, diff và arbiter. Không biến “4/5” thành quyền deploy hoặc “< 3/5” thành vòng lặp retry vô hạn. Thử dùng tín hiệu để ưu tiên đọc phần đáng ngờ; đo lỗi bỏ sót và số lần làm lại không cần thiết. |
| 7 | Gắn nhãn hàng loạt — `Choice` | CLI đã nhận nhiều item, nhưng [`decide.py`](../cli/src/ai_skill_jev/decide.py#L103) gửi từng request body của mỗi item; một lệnh CLI không đồng nghĩa một HTTP request. Chỉ thử gộp các câu hỏi độc lập dùng chung state khi số liệu cho thấy overhead đáng kể. Đếm và tổng hợp bằng code; không giấu item lỗi/abstain hoặc gộp dữ liệu khác phạm vi chia sẻ. [Benchmark batch của TypeSafe](https://docs.typesafe.ai/cookbooks/parallel_questions.md) đo trên một tài liệu với 13 câu hỏi, không phải kết quả của CLI này. |
| 8 | Điều khiển thời gian thực — `Choice` | Ngoài phạm vi skill điều phối công việc trong repo. Giữ checkpoint, timeout và fallback hiện có; không chèn Jev vào mỗi tick. [Demo độ trễ của nhà cung cấp](https://docs.typesafe.ai/demos/airbnb-listing-search.md) không phải SLA end-to-end; retry, rate limit và budget của CLI vẫn áp dụng. |
| 9 | Cổng độ tin cậy — chính sách trong code | Đã có [`judge_noul`, `judge_choice`, `judge_score`](../cli/src/ai_skill_jev/systemone.py#L196). `Noul` là P(yes), không có field `confidence`; Choice có xác suất từng nhãn; Score có phân phối trên các mức có thứ tự. `confidence` của Choice/Score mô tả mức tập trung của phân phối, không phải xác suất đáp án đúng. Giữ abstain khi thiếu căn cứ, Choice đảo thứ tự không đồng thuận, hoặc Score phân tán. Chọn ngưỡng theo từng câu hỏi và hậu quả sai; không dùng `0.9` hay `0.5` chung cho mọi hành động. Xem [Primitives](https://docs.typesafe.ai/primitives.md) và [Confidence](https://docs.typesafe.ai/confidence.md). |

### Thứ tự đề xuất cho repo này

1. **Sửa các lỗi dữ liệu, fallback, luật cứng và eval ở trên trước.** Thêm ngưỡng coverage tối thiểu trên tập *đáng lẽ quyết định* (không tính mẫu cố ý cần abstain); công bố riêng tỷ lệ `decided`, `abstain`, `unavailable` và lỗi theo hướng không an toàn. All-abstain không được PASS nếu tập có mẫu cần quyết định. Giữ `kinds: []` cho tới khi có kết quả đạt trên đúng provider/endpoint/model/template theo [hợp đồng eval](../cli/eval/README.md).
2. **Thử `leads` và `evidence` trước ở chế độ chỉ ghi nhận**, trên dữ liệu được phép gửi. Coordinator vẫn dùng luồng cũ. Dùng cặp run có/không có tín hiệu trên cùng nhiệm vụ; đo top-k recall cho `leads`, check thiếu bị bỏ sót cho `evidence`, và p50/p95 end-to-end, số HTTP attempt, token/cost và công làm lại cho cả hai. Đặt ngưỡng chấp nhận trước khi đọc kết quả. Chỉ bật từng kind nếu tăng chất lượng hoặc giảm chi phí/thời gian mà không hạ mức kiểm chứng.
3. **Chỉ sau đó cân nhắc `jobs` và batch.** Không thêm inbox chung, kiểm tra mọi tool hay vòng lặp real-time nếu chưa có nhu cầu đo được. Không dùng model để mở approval, quyết định `done`, bỏ arbiter hoặc mở rộng scope.

**Phạm vi xác minh phần bổ sung:** đối chiếu source hiện tại với tài liệu và cookbook TypeSafe; không gọi Jev thật, không đo benchmark và không sửa skill/CLI.
