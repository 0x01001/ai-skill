# Review: Jev workflow integration plan

- Ngày review: 2026-11-14.
- Plan: [2026-11-14-jev-workflow-integration-plan.md](../plan/2026-11-14-jev-workflow-integration-plan.md).
- Kết luận: **Cần sửa trước khi triển khai**.
- Phạm vi: đối chiếu đặc tả với CLI, ba skill ai-main/ai-auto/ai-orchestrate, references và tài liệu Jev. Không sửa implementation; không chạy tests vì đây là review đặc tả.

Có **8 finding đáng xử lý**. Các điểm dưới đây là lỗi trong đặc tả hoặc xung đột với workflow hiện có, không phải lỗi đã được xác nhận trong implementation mới. Số dòng tham chiếu theo phiên bản được review.

> **Verify (2026-11-14):** đã đối chiếu lại từng finding với plan, [cli.py](../cli/src/ai_skill_jev/cli.py), [models.md](../skills/ai-orchestrate/references/models.md), [ai-auto](../skills/ai-auto/SKILL.md) và [model-jaggedness](model-jaggedness.md). Cả 8 finding đúng và mọi trích dẫn dòng khớp. Đã chỉnh: F2 (bỏ nhận định quá mức về input, thêm xung đột với Rule 1), F4 (nêu rõ vòng lặp thứ tự `claims` ↔ `finding_scope`), F5 (thêm dòng 97), F6 (thêm điểm gọi sai gate trong ai-auto), F7 (dòng 151 là mơ hồ chứ không mâu thuẫn; thêm danh sách kind sai và deadline không đủ cho lô 60 request).

## 1. [P1] `claims` tự đọc file nhưng chưa có ranh giới filesystem đáng tin cậy

**Vị trí:** [plan:339–342](../plan/2026-11-14-jev-workflow-integration-plan.md#L339).

Đường dẫn trích dẫn đến từ báo cáo worker, nhưng CLI tự đọc rồi gửi nội dung tới provider. Plan chỉ kiểm tra file tồn tại, exclusion và phạm vi dòng; chưa xác định trusted root, revision, hay xử lý `../`, absolute path, symlink/junction.

Một citation trỏ ra ngoài repository có thể khiến CLI gửi nội dung file ngoài phạm vi. Regex secret không thay thế được kiểm soát đường dẫn. Ngoài ra, đọc nhầm checkout hoặc revision có thể biến citation đúng thành `unsupported`.

**Sửa:** truyền root và commit của nguồn cần kiểm từ coordinator; chỉ đọc path nằm trong nguồn đó, chặn traversal và link thoát root, áp exclusion trước khi đọc/gửi. Thêm trường hợp kiểm tra path escape và citation trên worker commit khác integration commit.

## 2. [P1] Mapping `jobs` làm hạ tier của reviewer

**Vị trí:** [plan:220–225](../plan/2026-11-14-jev-workflow-integration-plan.md#L220).

`intent` có `review`, nhưng mapping áp `R0 → t1/low` mà không có ngoại lệ cho review. Trong khi [models.md:24](../skills/ai-orchestrate/references/models.md#L24) yêu cầu review/arbiter/advisor **cao hơn strongest writer một tier, tối thiểu t3, effort high**, rồi mới áp ceiling.

Ngay cả khi `cmd_check = no` nâng effort lên high, tier vẫn sai. Khi review job nằm cùng lô với các writer, code có thể tính strongest writer sau khi quyết xong các job khác, nhưng plan không nói việc đó. Khi review job ở lô khác (fix job hay review thêm sau này), input không có trường nào mang tier của writer.

Cùng đoạn mapping còn hai lỗ hổng:

- `cmd_check = no → high` (dòng 225) áp cho mọi job, trong khi [models.md:30](../skills/ai-orchestrate/references/models.md#L30) Rule 1 coi job read-only là đã có check bằng lệnh khi báo cáo trích `file:line`, nên scout `t1` bắt đầu ở `low`. Áp nguyên dòng 225 thì mọi scout lên `high`.
- `cause_known` chỉ được hỏi cho job sửa lỗi (dòng 213). Job R1 `implement` không phải sửa lỗi thì không khớp nhánh nào ở dòng 221–223.

**Sửa:** xử lý review trước nhánh R0; giữ nguyên quy tắc trong `models.md`. Tính sau khi biết tier writer: lấy từ các job cùng lô, hoặc thêm input `strongest_writer_tier` cho lô khác. Áp pin/cap cuối cùng. Ghi rõ precedence cho `heavy` để tránh R0 deep research bị chọn t1. Miễn `cmd_check` cho job R0 có báo cáo trích `file:line`. Định nghĩa nhánh cho R1 `implement` khi không có `cause_known`.

## 3. [P1] Gate vẫn phụ thuộc engine dù `decide` được thiết kế không cần engine

**Vị trí:** [plan:25–28](../plan/2026-11-14-jev-workflow-integration-plan.md#L25), [plan:420](../plan/2026-11-14-jev-workflow-integration-plan.md#L420), [plan:441](../plan/2026-11-14-jev-workflow-integration-plan.md#L441).

Plan yêu cầu `state: ready` cho cả hai công cụ. Nhưng `doctor_report()` hiện trả `state: off` khi engine thiếu hoặc `jg` bị shadow: [cli.py:425–443](../cli/src/ai_skill_jev/cli.py#L425).

Chỉ thêm field `"decide": "ready"` không giải quyết được: skill vẫn kiểm `state` và bỏ qua `decide` dù key/provider dùng được.

**Sửa:** gate `decide` bằng capability riêng `tool: ai-skill` + `decide: ready`; giữ gate `jg` riêng. Đồng bộ điều kiện này ở §2, §3 và toàn bộ skill. Kiểm tra tình huống key hợp lệ nhưng không có engine.

## 4. [P1] “Safety → fix job” có thể tự mở rộng scope đã Confirm

**Vị trí:** [plan:299–305](../plan/2026-11-14-jev-workflow-integration-plan.md#L299), [plan:349](../plan/2026-11-14-jev-workflow-integration-plan.md#L349).

Mọi finding có `safety ≥ 0.5` bị ép thành `in_scope`, rồi thành fix job, kể cả lỗi có sẵn ngoài phạm vi run. Điều này xung đột với [ai-auto:55–60](../skills/ai-auto/SKILL.md#L55): finding ngoài scope không được sửa trong run; thay đổi scope phải hỏi user.

R3 approval không bảo vệ được trường hợp này: sửa một lỗi bảo mật ngoài scope có thể chỉ là local write R2.

Thứ tự gọi còn vòng lặp: [plan:423](../plan/2026-11-14-jev-workflow-integration-plan.md#L423) gọi `claims` **trước** `finding_scope`, nhưng dòng 349 dùng `safety` (chỉ có sau `finding_scope`) để quyết finding có bỏ qua kết quả `claims` hay không.

**Sửa:** tách **mức độ an toàn** khỏi **quyền sửa trong scope**. Safety finding ngoài scope phải được báo và chuyển qua quy tắc dừng/hỏi hiện có, không tự trở thành authorized fix. Chốt một thứ tự duy nhất (ví dụ chạy cả hai rồi mới hợp nhất) và bảng kết hợp `claims` × `scope` × `safety` → hành động (fix job / trả arbiter / báo cáo / hỏi user), áp trước khi tạo job hoặc trả finding về arbiter.

## 5. [P1] Cắt input có thể làm mất điều kiện quyết định nhưng vẫn trả kết quả dứt khoát

**Vị trí:** [plan:97](../plan/2026-11-14-jev-workflow-integration-plan.md#L97), [plan:125–129](../plan/2026-11-14-jev-workflow-integration-plan.md#L125), [plan:254–260](../plan/2026-11-14-jev-workflow-integration-plan.md#L254), [plan:373–386](../plan/2026-11-14-jev-workflow-integration-plan.md#L373).

Plan cắt field nhưng chưa truyền trạng thái coverage vào quyết định:

- Bằng chứng nằm sau phần báo cáo bị cắt có thể bị kết luận là thiếu và trả job về worker.
- Injection nằm ngoài 6.000 ký tự được kiểm có thể không bị phát hiện.
- Điều kiện scope ở cuối request có thể mất trước khi chọn lane.

**Sửa:** quy định theo từng kind: input bị cắt không đủ căn cứ thì `abstain`, hoặc chia đoạn và tổng hợp. Với `guard`, phải nêu rõ chỉ phần đã kiểm được chuyển tiếp; không cho kết quả kiểm prefix đại diện cho toàn bộ nội dung.

## 6. [P2] `guard` chưa có đường thực thi để chạy trước context của model

**Vị trí:** [plan:369–387](../plan/2026-11-14-jev-workflow-integration-plan.md#L369), [plan:422–423](../plan/2026-11-14-jev-workflow-integration-plan.md#L422).

Plan hứa kiểm trước khi nội dung ngoài đi vào context của bất kỳ model nào. Nhưng giao diện nhận `{source, text}` do agent cung cấp, còn tích hợp chỉ thêm bước gọi trong skill. Nếu agent đọc issue/tool output rồi mới dựng JSON, nội dung đã vào context coordinator trước guard.

Điểm gọi trong ai-auto cũng muộn: dòng 422 đặt `guard` cho issue body/comment ở Gate 3 bước 5, "trước khi chép vào brief". Nhưng `brief.md` được viết và Confirm ở [Gate 1](../skills/ai-auto/SKILL.md#L13), từ chính issue đó. Đến Gate 3 thì nội dung issue đã nằm trong brief.

**Sửa:** chọn một contract rõ ràng:

- Guard chỉ bảo vệ **worker brief**, không tuyên bố bảo vệ context coordinator; hoặc
- Fetch nội dung vào file ngoài model context, chạy guard từ file, rồi mới expose nội dung được phép.

Không cần thêm framework, nhưng phải có đường dữ liệu thực hiện được lời hứa này. Với ai-auto, chuyển `guard` cho issue lên Gate 1, trước khi soạn brief.

## 7. [P2] Batch chưa định nghĩa đủ lỗi từng item và cách tính retry vào budget

**Vị trí:** [plan:90–100](../plan/2026-11-14-jev-workflow-integration-plan.md#L90), [plan:144–151](../plan/2026-11-14-jev-workflow-integration-plan.md#L144).

Plan định nghĩa mixed `decided/abstain`, nhưng chưa nói kết quả khi một số item timeout, một item nhận `auth_error`, hoặc deadline hết trước khi các item còn lại được gửi. Exit 0 khi có một item thành công cũng chưa nói agent nhận tín hiệu ngừng gọi Jev từ item lỗi auth bằng cách nào.

Budget gọi là số **request**, nhưng một item có thể retry thêm hai lần; chưa rõ mỗi HTTP attempt có tiêu counter không. Dòng 151 “một decide luôn là 1 request” mơ hồ: có vẻ muốn nói một item là một request, nhưng đọc ngay sau dòng 144 (một lần gọi tối đa 60 request) thì dễ hiểu là cả lệnh `decide`.

Deadline cũng không khớp với kích thước lô: một lần gọi tối đa 60 request, song song 4 (dòng 55), tức ít nhất 15 đợt, mỗi request timeout 20 s. Deadline tổng 30 s (dòng 100, 150) không đủ cho lô lớn, nên partial failure là trường hợp thường gặp chứ không phải ngoại lệ. Plan chưa nói 30 s áp cho mỗi item hay cả lệnh.

Danh sách kind nhiều item ở dòng 90 (`jobs`, `findings`, `evidence`, `leads`) cũng không khớp mục 4. Không có kind tên `findings` (là `finding_scope`). Danh sách thiếu `claims`, `check_output`, `guard`, dù các kind này cũng mỗi item một request. Còn `evidence` thực ra là một báo cáo, một request.

**Sửa:** định nghĩa status/reason cho từng item, trạng thái batch khi partial failure, và quy tắc hủy item chưa gửi. Chốt đơn vị budget; nếu là HTTP request thì retry phải được tính, chỉ hoàn reservation chưa gửi. Chốt deadline theo item hay theo lệnh và sửa cho phù hợp với lô 60. Liệt kê đúng các kind nhiều item.

## 8. [P2] Mock tests không chứng minh các template đủ đúng để tự điều khiển workflow

**Vị trí:** [plan:448–460](../plan/2026-11-14-jev-workflow-integration-plan.md#L448), [plan:471](../plan/2026-11-14-jev-workflow-integration-plan.md#L471).

Toàn bộ test dùng HTTP giả, nhưng kết quả `decided` được dùng ngay để chọn lane, bỏ prerequisite, bỏ tạo issue hoặc trả job về worker. Mock kiểm được parsing/mapping, không kiểm được câu hỏi và ngưỡng có phân loại đúng dữ liệu thực tế hay không.

Tài liệu nguồn đã cảnh báo state mang nội dung đối kháng có thể làm lệch đáp án: [model-jaggedness:100–104](model-jaggedness.md#L100). [Hướng dẫn confidence](https://docs.typesafe.ai/confidence.md) cũng yêu cầu thử ngưỡng trên dữ liệu của ứng dụng.

**Sửa:** thêm bộ mẫu có nhãn nhỏ cho các quyết định làm thay đổi workflow, gồm tiếng Việt, dữ liệu thiếu và prompt injection; đánh giá bằng model được pin trước khi bật tự động. Giữ mock tests cho CI, nhưng không coi chúng là bằng chứng chất lượng phân loại.
