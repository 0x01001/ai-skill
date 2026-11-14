# Báo cáo đề xuất cải tiến bộ ai-skill

- Ngày: 2026-11-14
- Phạm vi: `ai-main`, `ai-auto`, `ai-orchestrate`, các reference và `scripts/sessions.py`.
- Hình thức: đánh giá và đề xuất; chưa sửa các skill hoặc script.
- Hướng chính: sửa các điểm không nhất quán trước, rồi giảm thủ tục; chưa cần thêm framework điều phối.

Các đường dẫn bằng chứng dưới đây tính từ root repository. Số dòng phản ánh nội dung tại thời điểm đánh giá.

## 1. Ưu tiên cao: phân biệt mất dấu session với tiến trình đã dừng

### Bằng chứng

- `skills/ai-orchestrate/scripts/sessions.py:74–80`: session biến mất sau khi từng được thấy, hoặc sau 60 giây, được đánh dấu `settled`; mọi `state` khác `working` cũng vậy.
- `skills/ai-orchestrate/references/recovery.md:11`: dùng `sessions.py status` và `git status` để xác nhận writer đã biến mất rồi dispatch lại.

Đã chạy phép kiểm tra trực tiếp, không tạo file hay gọi runtime Claude:

```bash
python -B -c "import runpy; m=runpy.run_path('skills/ai-orchestrate/scripts/sessions.py'); f=m['is_settled']; print('missing_after_seen =', f(None, True, 1)); print('missing_after_grace =', f(None, False, 61)); print('unknown_state =', f({'state':'unknown'}, False, 0)); print('working =', f({'state':'working'}, True, 1))"
```

Kết quả quan sát được:

```text
missing_after_seen = True
missing_after_grace = True
unknown_state = True
working = False
```

### Rủi ro

Session registry có thể mất dấu nhưng tiến trình vẫn sống; `git status` không chứng minh tiến trình đã dừng. Nếu coordinator dùng trạng thái này để dispatch lại, có thể tạo hai writer trên cùng worktree. Đây là kịch bản rủi ro suy ra từ logic, chưa tái hiện hai writer chạy đồng thời.

### Đề xuất tối thiểu

- Tách “cần coordinator xử lý” khỏi “đã xác nhận tiến trình dừng”.
- `missing`, trạng thái lạ và timeout được coi là `unknown`, không tự cho phép retry.
- Chỉ dispatch lại sau bằng chứng dừng từ runtime. Với Orca, dùng trạng thái và hành động `next` của dispatch.

## 2. Ưu tiên cao: đường dẫn script phải theo nơi cài skill

### Bằng chứng

`README.md:30–31` hỗ trợ cài global, nhưng lệnh fallback ở `skills/ai-orchestrate/SKILL.md:59` và `skills/ai-orchestrate/references/sub-sessions.md:34` cố định:

```text
<repo-root>/.claude/skills/ai-orchestrate/scripts/sessions.py
```

### Tình huống lỗi

Cài bằng `-g`, repo không có bản local: lệnh gọi script trỏ sai nơi cài. Chưa chạy thử một bản cài global riêng; nhận định dựa trên mâu thuẫn giữa đường dẫn và cách cài được tài liệu hỗ trợ.

### Đề xuất tối thiểu

Resolve thư mục của skill đang được nạp một lần; mọi reference gọi `scripts/sessions.py` từ thư mục đó. Giữ `<repo-root>` cho dữ liệu dự án, không dùng nó để suy ra nơi cài skill.

## 3. Ưu tiên vừa: thống nhất quy tắc chọn lane

### Bằng chứng

Trong `skills/ai-main/SKILL.md`:

- Dòng 22: scope chưa chắc thì đi `ai-auto`.
- Dòng 30: chọn `ai-auto` khi người dùng giao end-to-end và scope hoặc prerequisite chưa chốt.
- Dòng 32: sửa một file luôn Direct.

### Tình huống lỗi

Yêu cầu sửa cơ chế auth trong một file nhưng chưa rõ hành vi mong muốn đồng thời khớp nhiều quy tắc. Đây là mâu thuẫn trong hướng dẫn, chưa có đánh giá hành vi model trên tập tình huống.

### Đề xuất tối thiểu

Quy định thứ tự ưu tiên:

1. `--resume`: phục hồi run.
2. Chưa rõ tiêu chí hoàn thành: làm rõ phạm vi.
3. Đã rõ phạm vi: chọn Direct hoặc orchestration theo phụ thuộc, isolation và delivery.

Bỏ chữ “always” khỏi quy tắc một file. Số file không đủ để quyết định lane.

## 4. Ưu tiên vừa: Prepare chỉ kiểm tra prerequisite liên quan

### Bằng chứng

`skills/ai-auto/SKILL.md:33–44` liệt kê branch protection, GitHub labels, auth và tài nguyên máy; điều kiện qua gate là mọi prerequisite sẵn sàng hoặc được ghi nhận skipped.

### Vấn đề

- Chưa có điều kiện rõ cho từng nhóm kiểm tra; tác vụ local dễ bị kéo vào chuẩn bị GitHub không cần thiết.
- Chưa phân biệt prerequisite tùy chọn với prerequisite bắt buộc để chứng minh `Done means`.

### Đề xuất tối thiểu

- Chỉ kiểm tra GitHub khi delivery hoặc tracking cần GitHub.
- Chỉ kiểm tra headroom cho worktree/build khi kế hoạch dùng chúng.
- Prerequisite bắt buộc còn thiếu: block hoặc người dùng sửa contract; ghi “skipped” không tự mở Execute.

## 5. Ưu tiên vừa: để runtime sở hữu giao thức của nó

### Bằng chứng

- `skills/ai-orchestrate/references/cross-runtime.md:9–12` yêu cầu resolve và truyền model/effort tường minh cho mọi runtime.
- Dòng 31 yêu cầu tuân thủ skill Orca.
- Guide Orca nạp bằng `orca skills get orchestration` trong phiên đánh giá chỉ thêm `--model` khi người dùng chỉ định.
- `skills/ai-orchestrate/SKILL.md:79` mô tả Resume qua `sessions.py status`, vốn chỉ đọc Claude sessions.

### Rủi ro

Quy tắc chung có thể xung đột với giao thức Orca, hoặc coordinator dùng công cụ theo dõi Claude cho job của runtime khác.

### Đề xuất tối thiểu

Giới hạn quy tắc model/effort và `sessions.py` vào runtime tương ứng. Orca dùng guide đúng phiên bản để launch, nhận kết quả, resume và cleanup; `run.md` lưu Task/Dispatch ID để đối chiếu. Không cần viết một lớp adapter tổng quát.

## 6. Sau đó mới rút gọn prompt và thêm kiểm tra

Đây là đề xuất cải tiến thiết kế, không phải lỗi runtime đã được tái hiện.

### Giữ một nguồn cho mỗi quy tắc

- Routing ở `ai-main`.
- Xác nhận phạm vi ở `ai-auto`.
- Lifecycle ở `ai-orchestrate`.
- Reference chứa cơ chế theo runtime.
- Truyền các guardrail cần thiết vào worker brief thay vì lặp toàn bộ quy tắc khắp tài liệu.

### Bộ kiểm tra nhỏ đề xuất

- Mất dấu session không tự cho phép retry.
- Brief bị sửa phải xác nhận lại.
- Cài global tìm đúng script.
- Yêu cầu một file nhưng scope mơ hồ không tự vào Direct.
- Orca Resume không gọi Claude helper.

Các kiểm tra này chưa được bổ sung hoặc chạy, ngoại trừ phép kiểm tra hàm `is_settled` đã ghi ở mục 1.

## Thứ tự triển khai đề nghị

1. Lifecycle và bằng chứng tiến trình đã dừng.
2. Đường dẫn theo nơi cài skill.
3. Quy tắc routing thống nhất.
4. Prepare theo prerequisite thực sự cần.
5. Giao thức riêng theo runtime.
6. Rút gọn tài liệu và bổ sung kiểm tra.

Ba mục đầu có logic hoặc mâu thuẫn cụ thể làm bằng chứng, không chỉ là sở thích viết prompt.

## Phạm vi kiểm chứng và giới hạn

- Đã đọc trực tiếp ba skill, năm reference của `ai-orchestrate`, README và `sessions.py`.
- Đã chạy phép kiểm tra hàm `is_settled`; kết quả được ghi nguyên văn ở mục 1.
- Đã thử dispatch hai worker Codex qua Orca để review độc lập. Cả hai thất bại tại `agent_readiness`, `lastError=timeout`, liveness `unverifiable`; không có báo cáo độc lập.
- Dispatch `ctx_de21fcef3868` và `ctx_deb287d65fc9` đã được release theo hành động `next` của Orca. Cả hai trả về `state=released`, `processAction=closed_agent_terminal`.
- Chưa chạy quy trình end-to-end, thử cài global hoặc đo chất lượng routing của model.
- Chưa sửa nội dung skill hoặc script; báo cáo này là đầu ra của đợt đánh giá.
