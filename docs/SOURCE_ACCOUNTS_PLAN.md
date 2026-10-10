# Kế hoạch: tài khoản nguồn phim và tải bằng vé

Ngày: 2026-10-07 (Asia/Bangkok).

## 1. Trạng thái và phạm vi được người dùng đồng ý

- Người dùng đồng ý giao diện có combobox nguồn cần đăng nhập, tự đăng nhập trên trang chính thức, giữ phiên riêng và chạy ẩn khi lấy vé/tải.
- Nếu chưa kiểm tra được phiên thực tế: cần đăng nhập lại sau 3.600 giây kể từ lần đăng nhập thành công. Không ngắt file đang tải chỉ vì mốc này.
- Chỉ lỗi xác thực mới yêu cầu đăng nhập lại. Vé hết hạn được làm mới trước; lỗi mạng, máy chủ, file không còn và dung lượng được báo riêng.
- Tạo kế hoạch, giao từng mục cho Claude nếu có kênh kết nối; Codex chỉ thay Claude theo lựa chọn của người dùng. Không coi việc không kết nối được là bằng chứng Claude từ chối.
- Không triển khai vào thư mục chính, không merge/push, không restart Control Center đang dùng.
- Nhánh mới đã tạo: `feat/download-source-accounts`.
- Worktree mới: `E:\DungChung\BiliFlow\temp\wt-download-source-accounts`.
- Base: `e8aea11758ca75d7ee2395cb360e635b55e85f8f`.
- Trạng thái hiện tại: M0, M1 (mục 9.8), M2a (mục 9.9), M2b (mục 9.11) và M3 (mục 9.12) xong; ba lỗi P2 của review M3 đã sửa và Codex đã xác nhận; lỗi driver Playwright treo sau khi Edge đóng đã sửa (mục 9.13) và Codex đã duyệt; M4 xong (mục 9.14): bộ tải dùng manager của đúng root và tài khoản Windows, tác vụ chờ đăng nhập không giữ slot, trang phim nhiều tập lưu danh sách và tách thành nhóm tập; ba lỗi P2 của review M4 đã sửa (mục 9.15: Hủy nhóm hủy mọi tập chưa xong, ý định nhóm bền qua ngắt/restart, shutdown không đóng store khi luồng còn dùng) và Codex đã xác nhận; giai đoạn A ổn định test (mục 9.16) đạt cổng: sau khi sửa race của `SharedSpaceTests` (prompt bổ sung), full suite trên code cuối 2.380 test, 0 FAILURE/ERROR, 26 skip có lý do; M5 (giao diện, mục 9.17) xong 2026-10-09 sau cổng này; review M5 của Codex có một P2 (nút tài khoản PC hiện khi không xác định được chế độ trang), đã sửa ở mục 9.18 và Codex đã duyệt lại, chốt M5; M6 (kiểm tích hợp bằng fixture, mục 9.19) xong 2026-10-09: ma trận yêu cầu → test, harness đầu-cuối qua Dashboard V2 với route và bộ tải thật trên root fixture, 7 lỗi production sửa kèm test đỏ trước; chờ Codex review trước M7. M1 thêm thư viện (cấu hình, kho phiên mã hóa, session manager); M2a thêm lớp mạng của trình duyệt có phiên (Edge headless, mọi yêu cầu qua một client đã kiểm); M2b thêm thư viện điều phối cửa sổ đăng nhập (chỉ mở khi người dùng bấm, chỉ lưu phiên khi bộ xác nhận của adapter thấy bằng chứng từ nguồn); M3 thêm provider của nguồn tài khoản: đọc danh sách phim/mùa/tập/bản của trang phim đã dán bằng phiên (headless), lấy vé đúng một file đã chọn và tải file qua `SafeHttp` không cookie với kiểm phiên bản nghiêm ngặt. Chưa nguồn thật nào có bộ đọc trang hay bộ xác nhận đăng nhập; cửa sổ có giao diện chưa được người dùng kiểm. Registry giao mọi host của nguồn đã cấu hình cho provider tài khoản (link đó không sang yt-dlp hay provider ẩn danh); từ M4 Control Center tạo manager của root, có trạng thái WAITING_LOGIN và EXPANDED, nhóm tập, route chọn tập/nhóm (điện thoại được dùng) và route đăng nhập PC-only; từ M5 Dashboard V2 có khung tài khoản nguồn phim, hộp chọn tập và tiến độ nhóm tập. Chưa commit.
- 2026-10-07: M0 xong, chỉ có tài liệu.
  - Đã cập nhật phạm vi trong `AGENTS.md` và `docs/VIDEO_DOWNLOAD_PLAN.md` (mục 2, 4.11, 4.14, 9).
  - Kết quả đối chiếu code và thiết kế đề xuất ở mục 9.
  - Chưa có code, test, cấu hình, route hay giao diện nào của tính năng này. Chưa commit.
- 2026-10-07, sau khi Codex kiểm tra M0, người dùng chọn:
  - giữ MKV như cơ chế hiện có, chưa thêm remux sang MP4;
  - ACL thư mục phiên gồm tài khoản Windows đang chạy BiliFlow, SYSTEM và Administrators, tắt kế thừa, không giữ Authenticated Users, Users, CodexSandboxUsers hay SID lạ. Chỉ áp dụng cho thư mục phiên riêng (test dùng root tạm), không đổi quyền thư mục chính;
  - DPAPI theo người dùng hiện tại, không dùng `CRYPTPROTECT_LOCAL_MACHINE`, không thêm dependency;
  - để M2 kiểm chứng A hay B (mục 9.4); M1 không phụ thuộc proxy hay trình duyệt cụ thể;
  - không thêm pause riêng cho bộ tải.
- 2026-10-07: M1 xong theo các lựa chọn trên và ba chỉnh lý tài liệu của Codex (9.1 FileTransfer, 9.6 mã 407, 9.4 không nới yêu cầu mạng). Kết quả, bằng chứng và giới hạn ở mục 9.8.
- 2026-10-07, review M1 của Codex (P2): kho phiên đã chia theo SID nhưng dòng DB chỉ khóa theo `source_id`, nên tài khoản Windows B có thể hạ, thay hoặc khiến A tự dọn phiên của A. Đã sửa: trạng thái theo SID + nguồn (mục 9.8). Chờ Codex review lại trước M2.
- 2026-10-07: Codex duyệt lại M1 (103 test liên quan và 3 regression độc lập đạt) và cho làm M2a: chỉ kiểm chứng và làm lớp mạng cho trình duyệt có phiên, chưa làm toàn bộ M2. Xong 2026-10-08 (mục 9.9); chờ Codex review trước M2b.
- 2026-10-08: Codex review M2a, có một lỗi P2: cookie giữ nguyên giá trị mà đổi thuộc tính thì không được lưu. Đã sửa (mục 10); chờ Codex review lại trước M2b.
- 2026-10-08: người dùng bổ sung yêu cầu "dán trang phim và chọn tập". Vẫn dán link trang phim vào ô tải hiện có. Phim nhiều tập có hai lựa chọn: Tải tất cả các tập đang có, hoặc Chọn tập. Thiết kế và cách chia vào M2b–M7 ở mục 9.10. Lượt này chỉ cập nhật kế hoạch, chưa có code cho phần này.
- 2026-10-08: Codex review lại M2a đạt (regression cookie 1/1, 109/109 test). Người dùng giao M2b theo prompt riêng (luồng đăng nhập chủ động và dùng lại phiên ẩn). Xong cùng ngày (mục 9.11): code và test, chưa nối API, giao diện hay hàng đợi. Chờ Codex review trước M3.
- 2026-10-08: Codex review M2b, có một lỗi P2: hết hạn hay shutdown trong lúc đọc trạng thái trình duyệt mà vẫn lưu phiên và trả CONNECTED. Đã sửa (mục 9.11, phần "Có bằng chứng"; nhật ký ở mục 10). Chờ Codex review lại trước M3.
- 2026-10-08: Codex review lại M2b đạt. Người dùng giao M3 theo prompt riêng: nhận link trang phim gốc, đọc danh sách tập/bản và lấy vé đúng file được chọn; chuẩn bị cho Tải tất cả / Chọn tập. Xong cùng ngày (mục 9.12): code và test, chưa nối hàng đợi, API hay giao diện. Chờ Codex review trước M4.
- 2026-10-08: Codex review M3, ba lỗi P2 (4 test độc lập FAIL): lượt ẩn hết hạn/hủy lúc đọc trạng thái vẫn trả kết quả; phân trang bị bỏ mà báo đầy đủ; tải nối nối 206 có ETag khác. Đã sửa trong worktree này (mục 9.13); 4 test độc lập đạt. Chờ Codex review lại trước M4.
- 2026-10-08: Codex xác nhận ba lỗi trên đã sửa (4 regression và 205 test liên quan đạt). Trước M4, người dùng giao sửa lỗi driver Playwright còn treo sau khi Edge đóng (mục 9.13). Đã sửa: sau khi kết thúc Edge, nếu lượt vẫn chưa đóng thì thêm một grace nữa và kết thúc driver của đúng lượt (chỉ qua handle của chính runtime đó), cho cả lượt ẩn lẫn cửa sổ đăng nhập. Chờ Codex review trước M4.
- 2026-10-08: Codex duyệt bản sửa driver treo. Người dùng giao M4 (nối hàng đợi/API, chờ đăng nhập, nhóm tải theo tập). Xong cùng ngày (mục 9.14): code và test, chưa giao diện. Chờ Codex review trước M5.
- 2026-10-08: Codex review M4, ba lỗi P2 (3 test độc lập FAIL): Hủy nhóm bỏ sót tập đã dừng/bị ngắt/lỗi; nhóm đã hủy chạy tập sau khi bị ngắt và restart; shutdown đóng SQLite khi luồng còn dùng. Đã sửa (mục 9.15); 3 test độc lập đạt. Chờ Codex review lại trước M5.
- 2026-10-09: sau cổng A (mục 9.16), người dùng giao M5 theo prompt riêng: khung tài khoản nguồn phim, hộp chọn tập và tiến độ nhóm trên Dashboard V2. Xong cùng ngày (mục 9.17): chỉ giao diện, không đổi scheduler, lifecycle hay route. Chờ Codex review; chưa làm M6/M7.
- 2026-10-09: Codex review M5, một P2: GET `/api/phone-mode` lỗi (503/404) thì trang qua listener điện thoại vẫn hiện Đăng nhập lại/Ngắt kết nối (backend vẫn chặn). Đã sửa (mục 9.18): chỉ câu trả lời `remote === false` mới mở thao tác tài khoản; script độc lập của Codex đạt `--expect-pass`. Chờ Codex review lại trước M6.
- 2026-10-09: Codex duyệt lại và chốt M5; người dùng giao M6 theo prompt riêng (ngoài repo): kiểm tích hợp bằng fixture, có luồng đầu-cuối qua Dashboard V2 với route thật và bộ tải thật, ma trận yêu cầu → test. Xong cùng ngày (mục 9.19); dừng để Codex review trước M7.
- `main` đã tiến lên `47a1956` ("feat: turn anime gore rule C1 on"). Commit này chỉ đổi `gore_triage.py`, `review_workflow.py`, test gore và tài liệu, không đụng file nào của bộ tải. Worktree giữ base `e8aea11`; cập nhật base là việc người dùng quyết định (mục 6).

## 2. Kết quả xem code hiện tại

- `src/biliflow/download_embedded_source.py`: Edge headless, profile tạm cho mỗi lượt, xóa profile khi kết thúc; requests qua SafeHttp; không chuyển cookie hay Set-Cookie; đóng popup. Không thể dùng nguyên bộ đọc này để giữ phiên hoặc đọc vé mở ở tab mới.
- `download_http.SafeHttp`: không cookie/credentials, kiểm tra host, DNS/public address, TLS và từng redirect, timeout và bounded reads. Giữ mặc định này cho provider công khai và bộ tải media.
- `download_sources.DirectMediaProvider.claims`: nhận URL theo đuôi media hoặc `.m3u8`; URL dạng `/download` chưa được nhận diện bởi đường direct.
- `download_media_file.resolve_file` đã dùng GET Range để kiểm tra container và kích thước; FileTransfer hỗ trợ tiếp tục qua Range/If-Range.
- Queue có state và nhóm chiếm slot cố định. Cần bổ sung Chờ đăng nhập xuyên suốt store, worker, API, dashboard và khôi phục sau restart.
- AGENTS.md và docs/VIDEO_DOWNLOAD_PLAN.md hiện giới hạn provider ẩn danh. Trong worktree mới, cập nhật đúng phạm vi người dùng đã cho phép: kết nối tài khoản có chủ đích, phiên riêng, không lưu mật khẩu, không dùng profile trình duyệt cá nhân. Giữ giới hạn DRM/paywall/challenge bypass và kiểm tra mạng.

## 3. Sản phẩm và trải nghiệm người dùng

### Panel Tài khoản nguồn phim

Đặt trong trang Tải video, không tạo cửa sổ console. Có combobox nguồn được hỗ trợ và đã cấu hình; tên hiển thị không bắt buộc trùng ID nội bộ. Có trạng thái, lần đăng nhập gần nhất, thời hạn kiểm tra lại, Đăng nhập/Đăng nhập lại và Ngắt kết nối.

Trạng thái tối thiểu: Chưa kết nối; Đang đăng nhập; Đã kết nối; Cần đăng nhập lại; Không kiểm tra được kết nối. Lỗi mạng không tự chuyển một phiên còn tốt thành hết hạn.

Đăng nhập chỉ mở cửa sổ khi người dùng bấm nút. Người dùng nhập thông tin trên website chính thức, BiliFlow không có form thu mật khẩu. Chỉ ghi nhận thành công sau khi có bằng chứng từ trang hoặc endpoint của nguồn, không chỉ dựa vào việc đóng cửa sổ. Hủy/đóng cửa sổ không được báo thành công.

Panel và API status có thể hiển thị trên điện thoại; thao tác mở cửa sổ đăng nhập/ngắt kết nối ban đầu là PC-only với bảo vệ localhost/token như các thao tác cấu hình nhạy cảm hiện có. Điện thoại phải hiển thị lý do và hướng dẫn thực hiện trên PC, không âm thầm mở cửa sổ trên PC từ một thao tác không được phép.

### Khi dán link phim

Nhận đúng nguồn từ host chính xác. Các host trang phim, trang vé và máy chủ file thuộc một source ID, nhưng không mặc nhiên chia sẻ cookie cho mọi host đó. Không nhận dạng bằng substring hoặc wildcard.

Nguồn cần đăng nhập mà chưa có phiên: tác vụ chuyển Chờ đăng nhập, không chiếm slot; UI có nút dẫn đến đúng nguồn trong panel. Nguồn khác vẫn chạy.

Người dùng không phải tự lấy link media hay link vé: chỉ dán link trang phim. Adapter đọc trang, không đòi đường dẫn có tên phim cố định.

Phim lẻ (một file hợp lệ) đi theo luồng tải phim lẻ hiện có; nếu có nhiều bản chất lượng hay âm thanh thì người dùng chọn bản trước. Phim nhiều tập: hiện tên phim, mùa nếu có, tên hay số tập, các bản tải, và hai chế độ "Tải tất cả các tập đang có" hoặc "Chọn tập" (checkbox từng tập, chọn tất cả, bỏ chọn). Luôn thấy phạm vi sẽ tải và số tập đã chọn; nút ghi "Tải N tập". Chỉ khi người dùng bấm nút này mới có tác vụ tải. Chi tiết ở mục 9.10.

Đăng nhập thành công: các tác vụ đang chờ của nguồn đó trở về queue theo thứ tự vốn có, vẫn tôn trọng giới hạn slot và yêu cầu dừng/hủy của người dùng. (M0: bộ tải hiện không có "global pause"; nút tạm dừng `/api/scheduler` chỉ dành cho hàng GPU quét/xuất. Chỉ thêm pause cho bộ tải nếu người dùng yêu cầu, mục 9.7.) Không đánh thức tác vụ của nguồn khác. Người dùng đã hủy tác vụ thì không tự chạy lại.

## 4. Kiến trúc dự kiến

### A. Cấu hình nguồn

Giữ host thật, login URL thật và danh sách host liên quan trong cấu hình local bị Git ignore. Tài liệu/code/test public chỉ dùng `portal.example`, `tickets.example`, `files.example`.

Cấu hình chỉ chọn adapter có trong code, source ID, tên hiển thị và các host/URL hợp lệ; không chấp nhận lệnh shell, executable, Python import hay JavaScript tùy ý từ cấu hình. Login URL phải HTTPS, host chính xác đã cho phép. Các adapter hỗ trợ kiểm tra phiên và adapter fallback 1 giờ được phân biệt rõ.

### B. Session manager

Mỗi nguồn có phiên riêng dưới thư mục private bên trong project; không mở profile Edge/Chrome cá nhân và không nhập cookie của trình duyệt Codex. Phiên Codex dùng để khảo sát không phải phiên sản phẩm.

Ưu tiên giữ bộ trạng thái xác thực cần thiết của context do BiliFlow tạo, mã hóa khi lưu bằng Windows DPAPI theo tài khoản Windows hiện tại. Không để file storage-state JSON thô nằm lâu trên đĩa. Thư mục riêng chỉ tài khoản Windows hiện tại được truy cập; kiểm tra ACL thực tế vì project gốc đang được nhiều tài khoản quyền sửa. Không đặt trên ổ C và không thêm dependency khi không cần.

Cookie có scope domain/path/secure/expiry đúng chuẩn; storage theo origin; không chuyển credentials sang redirect hoặc host ngoài phạm vi. Không log password, cookie, session state, signed URL, ticket token hay request body đăng nhập. Không trả các dữ liệu đó trong API, kể cả API status.

Mỗi nguồn chỉ một hoạt động đăng nhập hoặc thay phiên tại một thời điểm. Kết quả có generation/version để tác vụ dùng phiên cũ không ghi đè phiên mới. Ngắt kết nối khóa việc lấy vé mới, xóa phiên đã lưu, không làm hỏng file media đã có link độc lập; phiên chỉ xóa trong source-owned directory đã kiểm tra đường dẫn.

Fallback TTL tính từ authenticated_at, không tự kéo dài bằng polling/chạy tác vụ. Với nguồn có verifier thật, kết quả verifier quyết định. Timeout mở cửa sổ chờ người dùng là giới hạn riêng, không dùng lẫn với TTL phiên. Hủy, restart và lỗi lưu state phải có kết quả rõ ràng.

### C. Đường browser có xác thực

Làm adapter riêng; không bật cookie cho mọi SafeHttp hoặc sửa anonymous adapter thành dùng profile chung.

Tất cả network/redirect vẫn phải qua kiểm tra mạng tương đương SafeHttp: không IP private, không DNS rebinding, TLS hợp lệ, giới hạn body và thời gian. Cookie chỉ thêm trong đường xác thực đúng source; Set-Cookie chỉ áp dụng đúng domain/path. Request body đăng nhập chỉ đi tới endpoint/origin đã cho phép và không ghi log.

Chỉ chế độ đăng nhập do người dùng bấm mới headed. Chế độ kiểm tra phiên/lấy danh sách/lấy vé luôn headless. Cho phép tab vé thuộc host của adapter; popup quảng cáo không trở thành nguồn media. Không đóng mọi popup trước khi phân loại vì tab vé hợp lệ cũng mở bằng popup.

Không vượt CAPTCHA, anti-bot, DRM hay paywall; khi website đòi thao tác người dùng, hiển thị trạng thái cần thao tác và chỉ mở UI khi người dùng bấm. Không sửa browser fingerprint để lách chặn.

### D. Provider danh sách file/vé tải

Đọc danh sách file từ vùng bản tải của trang, bỏ trailer/quảng cáo. Nếu nhiều tập/chất lượng thì người dùng chọn rõ ràng, không tự đoán tập hay bản. Không tự tải cả mùa khi người dùng chưa chọn. Khi người dùng chọn "Tải tất cả" và bấm "Tải N tập" thì đó là lệnh tải đúng các tập đã hiện trong phạm vi lựa chọn, không gồm tập đăng thêm sau này (mục 9.10).

Chỉ xin vé khi tác vụ chuẩn bị sử dụng slot tải. Đợi điều kiện/nút sẵn sàng theo luồng bình thường của trang, không dùng endpoint để bỏ qua điều kiện. Bắt đúng link Tải xuống cho file đã chọn, kiểm tra host/redirect và đọc bounded GET Range, không dùng HEAD làm bằng chứng duy nhất.

Trả ResolvedSource transport http_file; giữ media URL private. Danh tính dùng source + stable file ID/variant + kích thước/validator phù hợp, không dùng ticket token trong URL/path làm danh tính. Vé mới phải vẫn cùng file trước khi nối .part; không dựa vào tên hoặc kích thước đơn lẻ.

Định dạng MKV có thể remux sang MP4 nếu stream tương thích. Theo đối chiếu M0, hiện chưa có cơ chế này cho MKV: `FileTransfer` chỉ chép MPEG-TS sang MP4, còn MKV giữ đuôi `.mkv` khi vào `input` (watcher và import nhận `.mkv`). Người dùng chọn (2026-10-07): giữ MKV như cơ chế hiện có, chưa thêm remux. Không ghi đè source hoặc xóa user media; lỗi remux/verify giữ file tạm và báo rõ.

### E. Queue/API/UI

Thêm WAITING_LOGIN vào schema/state transitions, recovery, cancel/stop/remove, bộ lọc và thống kê dashboard. WAITING_LOGIN không thuộc SLOT_STATES; scheduler không loop hoặc liên tục xin vé khi thiếu phiên.

API status chỉ trả source ID, tên hiển thị, trạng thái, thời gian đã xác thực, cần đăng nhập lại và thông báo đã lọc. Các action kết nối/ngắt phải có kiểm tra PC/CSRF/token, không chạy thao tác nặng dưới database/global lock. Không có API xuất cookie.

Giữ public_url/log scrubbing cho URL token nằm trong path; source identity/API/probe cache không được làm lộ token. Không lưu signed URL trên bản ghi public. Link mới sau refresh không làm identity đổi chỉ vì ticket đổi.

### F. Phân loại lỗi

- Login redirect hoặc thông báo phiên không hợp lệ rõ ràng: WAITING_LOGIN của đúng nguồn.
- 403 đơn lẻ không đủ kết luận hết phiên: có thể rate limit, quyền hoặc anti-bot; cần bằng chứng của adapter.
- Ticket hết hạn: thử refresh có giới hạn nếu phiên còn tốt; nếu cần login thì chờ.
- Mạng/timeout/DNS: retry hoặc INTERRUPTED theo cơ chế hiện có, không mở cửa sổ đăng nhập.
- File xóa, lỗi nguồn, thiếu chỗ, source thay đổi: giữ mã lỗi tương ứng; không giả thành login.
- Expiry phiên không ngắt một FileTransfer có URL độc lập đang chạy; expiry URL là việc khác.

## 5. Chia việc thành từng mục, có điểm kiểm tra

Mỗi mục kết thúc bằng báo cáo: file thay đổi, hành vi, test đã chạy, kết quả, giới hạn. Không tự chuyển sang mục sau nếu còn lỗi của mục trước chưa được xử lý hoặc giải thích. Không hỏi lại những quyết định người dùng đã duyệt.

- [x] M0: kế hoạch và phạm vi. Đọc AGENTS/handoff/status/README/changelog theo thứ tự, xác nhận đúng branch/worktree; cập nhật docs scope chỉ trong worktree; không thay runtime. Báo cáo ranh giới code hiện tại và thiết kế. Xong 2026-10-07, chỉ có tài liệu (mục 9); chờ Codex kiểm tra trước khi giao M1.
- [x] M1: session manager và cấu hình. Implement TTL, trạng thái, generations, encryption/ACL, disconnect có phạm vi, fake verifier. Test clock giả, cancel login, restart, sai user Windows/corrupt vault, không lộ secrets, một nguồn không ảnh hưởng nguồn khác. Không gọi site thật. Xong 2026-10-07 (mục 9.8): 86 test mới đạt (cấu hình 11, kho phiên 19, session manager 44, phạm vi tài khoản 12), sau khi sửa các điểm của hai review code và lỗi P2 của review Codex (trạng thái theo tài khoản Windows); toàn bộ `test_download*` và test khóa cache quét đạt. Chưa nối vào runtime; chờ Codex review lại trước M2.
- [x] M2: browser đăng nhập/kiểm tra phiên. Explicit headed login, saved state và hidden resolver, kiểm tra mạng/cookie isolation, tab vé vs ads. Test fixtures, không lấy session cá nhân. Test headed cần người dùng hoặc fake launcher; không mở UI bất ngờ. Chia hai phần (tab vé và quảng cáo là việc của M3):
  - [x] M2a: lớp mạng của trình duyệt có phiên (chọn A hay B ở 9.4, cookie, redirect, chặn đường ngoài lớp kiểm tra, gắn kết quả với manager). Xong 2026-10-08 (mục 9.9): chọn B; test trình duyệt chạy trên Edge headless thật; Codex review lại đạt.
  - [x] M2b: cửa sổ đăng nhập chủ động (headed, chỉ từ nút bấm), xác nhận đăng nhập bằng bằng chứng của trang và tái sử dụng phiên ẩn. Giữ giao diện nội bộ của trình duyệt và phiên đủ cho việc đọc danh sách tập có phiên (mục 9.10); chưa cần bộ đọc trang thật. Xong 2026-10-08 (mục 9.11): thư viện `download_account_login.py` và test (fake launcher cho headed, Edge headless thật cho luồng fixture). Chưa nghiệm thu: cửa sổ có giao diện thật (cần người dùng) và bằng chứng đăng nhập của nguồn thật (M3/M7). Chờ Codex review trước M3.
- [x] M3: provider file/vé. Đọc variants, NEEDS_CHOICE, GET Range, /download, stable identity, refresh vé. Phân biệt phim lẻ, mùa/tập và bản (variant); danh tính ổn định cho phim, tập, file và bản; đọc đủ danh sách và phân trang được hỗ trợ, báo rõ khi chưa đủ; bỏ trailer/quảng cáo; vé chỉ cho đúng một file đã chọn (mục 9.10). Fixtures gồm ads, expired ticket, login redirect, Range và đổi file; kiểm tra metadata synthetic. Xong 2026-10-08 (mục 9.12): thư viện và test (fixture tự làm trên Edge headless thật, bộ đọc fixture). Chưa có bộ đọc trang hay bộ xác nhận của nguồn thật (cần cấu trúc trang thật, M7). Chờ Codex review trước M4.
- [x] M4: queue và API. WAITING_LOGIN không chiếm slot; recovery và mọi action cần thiết; source-specific wake, global pause và race cancel/login. Nhóm tác vụ theo tập: tạo theo thứ tự, lưu bền và chống tạo trùng, chỉ xin vé khi tập sắp dùng slot, tuần tự hóa thao tác trình duyệt của một nguồn, dừng/hủy/thử lại/khôi phục cho từng tập và cả nhóm; giữ nguyên giới hạn đồng thời của bộ tải (mục 9.10). API/phone/token/security tests. Chỉ test Control Center root temp/port riêng. Xong 2026-10-08 (mục 9.14): nối runtime tài khoản, route PC-only, WAITING_LOGIN, preview/nháp/xác nhận, nhóm tập có idempotency và tạo dần dưới 100, tên tập; chưa giao diện. Ba lỗi P2 của review M4 đã sửa cùng ngày (mục 9.15). Chờ Codex review lại trước M5.
- [x] M5: dashboard. Combobox, trạng thái, nút connect/disconnect, task CTA và filter; hộp chọn tập (Tải tất cả / Chọn tập, theo mùa, chọn bản, "Tải N tập") và tiến độ nhóm (mục 9.10); refresh không mất lựa chọn/focus. Kiểm tra light/dark, desktop và điện thoại; phone chỉ xem/hướng dẫn các thao tác PC-only. Fixture API, không production POST. Xong 2026-10-09 (mục 9.17): gate node mới, kiểm trên Edge headless với server giả, full suite trên code cuối 0 FAILURE/ERROR. Review của Codex có một P2 (nút tài khoản khi chế độ trang chưa xác định), đã sửa ở mục 9.18. Chờ Codex review lại trước M6.
- [x] M6: tích hợp và regression. Focused download/API/phone/dashboard suites + node gates; browser fixtures; full suite nếu thay scheduler/Control Center (synthetic 1 giây trong input của worktree, không copy video thật). Bộ fixture phim lẻ và phim nhiều tập ở mục 9.10. Kiểm tra diff/secrets, model/tool audit nếu dependency/license thay đổi. Không download package/tool mới nếu chưa có chấp thuận cần thiết. Xong 2026-10-09 (mục 9.19): ma trận tích hợp; E2E qua Dashboard V2 (E1–E7), crossing với provider thật (C1), test hàng đợi/route; 7 lỗi production sửa kèm test đỏ trước; full suite 2.433 test: 2.407 đạt + 26 skip, 0 FAILURE/ERROR. Không chứng minh nguồn thật (M7). Chờ Codex review trước M7.
- [ ] M7: nghiệm thu riêng. Người dùng bấm đăng nhập trong BiliFlow test instance, kiểm tra nguồn được chỉ định bằng bounded probe, không tải full phim. Người dùng thử một trang phim thật của họ: chỉ đọc metadata và thăm dò có giới hạn để xác nhận danh sách tập và lựa chọn, không tự tải toàn bộ phim. Xác nhận headless sau đăng nhập; mốc 1h bằng clock fake; phiên hết và link hết là hai test khác nhau. Đánh dấu điều gì đã thật sự qua nghiệm thu. Cập nhật CHANGELOG/PROJECT_STATUS/SESSION_HANDOFF ở worktree.

## 6. Quy tắc cách ly bản đang dùng

- Chỉ sửa file trong worktree mới. Thư mục chính chỉ đọc.
- Không merge main, push, reset checkout chính, restart real Control Center, hay đổi local config/state/input/output/reports của bản chính.
- Dùng Python/FFmpeg đã có ở project nhưng đặt PYTHONPATH = worktree/src; test roots/logs/private session/fixtures dưới worktree/temp. Không dùng production SQLite.
- Không POST tới /api/downloads*, /api/tailscale*, /api/source-cleanup*, /api/job-delete*, /api/source-archive* của real Control Center.
- Không lấy/di chuyển/xóa video thật. Chỉ dọn đúng fixture do test tạo.
- Nếu base main tiến lên, báo khác biệt trước khi quyết định cập nhật worktree; không làm merge thay đổi chính âm thầm.
- Khi cần review hay commit, ghi đúng milestone; không tuyên bố hoàn tất chỉ vì UI có panel.

## 7. Điều kiện hoàn thành

Người dùng chọn đúng nguồn, tự login một lần, dán trang phim và chọn file; tác vụ chạy ẩn và có tiến độ qua bộ tải hiện có. Phim nhiều tập: chọn Tải tất cả hoặc từng tập, bấm "Tải N tập"; mỗi tập là một tác vụ trong nhóm, đúng thứ tự mùa/tập, tên file có số thứ tự, tiến độ từng tập và "đã xong N/tổng"; refresh hay khởi động lại không mất lựa chọn, nhóm hay thứ tự, thử lại không tạo trùng. Khởi động lại test instance giữ phiên nếu còn hiệu lực. Thiếu/hết phiên chờ đúng nguồn, không giữ slot và không ảnh hưởng nguồn khác. Vé mới vẫn đúng file để resume. Mạng/403 không bị quy chụp hết login. Secrets không xuất hiện trong API/log/Git. Bản chính giữ nguyên. Không cần tải toàn bộ phim thật để xác nhận.

## 8. Bằng chứng và giới hạn

Một nguồn người dùng cung cấp đã được khảo sát qua UI có đăng nhập: danh sách file -> vé mở tab khác -> chờ nút -> link file. Bounded GET trả 206, Content-Type video/matroska, container MKV hợp lệ và Range ở offset khác thành công, không cần chuyển cookie vào bộ tải trong lần đó. HEAD lại trả HTML sau redirect, nên chỉ HEAD là không đủ.

Chi tiết host/film/token không được đưa vào repository public. Biên bản riêng của khảo sát là ghi chú riêng bên ngoài repo (`BILIFLOW-SOURCE-ACCOUNTS-PRIVATE-NOTES.md`). Không tái sử dụng ticket cũ hoặc phiên Codex làm dữ liệu triển khai. Chưa kiểm tra việc giữ phiên giữa headed/headless của sản phẩm, chưa kiểm tra full download và chưa chứng minh mọi nguồn hoạt động giống nhau.

## 9. Kết quả M0 (2026-10-07): đối chiếu code và thiết kế đề xuất

Mục này viết ở M0, là đối chiếu và đề xuất. Số dòng tính theo base `e8aea11`. Những gì M1 đã làm thật nằm ở mục 9.8 (bảng 9.2 ghi rõ dòng nào đã có). Các chỗ còn ghi "dự kiến" hay "đề xuất" là việc của M2–M7, chưa tồn tại.

### 9.1 Ranh giới code hiện tại (đã đọc)

| Chỗ | Hiện trạng | Hệ quả cho tính năng |
|---|---|---|
| `download_http.SafeHttp` | Không cookie, không proxy. Mỗi link và redirect qua `check_link` + DNS công khai, nối đúng địa chỉ đã kiểm. `_status_error`: 401/407 → `LOGIN_REQUIRED` với lời nhắn "BiliFlow không dùng tài khoản hay cookie" (dòng 180–182); 403 → `FORBIDDEN`. | Giữ nguyên cho provider ẩn danh và cho việc tải file media. Adapter tài khoản phân loại lỗi xác thực bằng bằng chứng của chính nó, không dựa vào lời nhắn này. |
| `download_embedded_source` | `route.fulfill` chỉ trả `Content-Type`, `Content-Range`, `Accept-Ranges`, nên `Set-Cookie` không bao giờ tới trình duyệt. Header gửi đi bỏ cookie. Mọi popup bị đóng. Ảnh, font và loại tài nguyên khác bị chặn. Profile tạm bị xóa sau mỗi lượt. | Không dùng lại được cho đăng nhập (trang không có ảnh, không giữ cookie, tab vé là popup) hay cho đọc có phiên. Cần adapter riêng, đúng như mục 4C. |
| `download_source_types` | `ResolveContext` không có chỗ cho phiên. `SourceError` luôn kết thúc FAILED, chưa có lỗi nào dẫn tới trạng thái chờ. | Cần một lỗi riêng kiểu "cần đăng nhập nguồn X" mang id nguồn và generation của phiên. |
| `download_source_steps` | PROBING: `SourceError`/`HttpError` → FAILED (dòng 101–104). DOWNLOADING: `RESUMABLE_CODES` (NETWORK, SERVER_BUSY, DNS_FAILED) → INTERRUPTED; lỗi khác → FAILED (dòng 164–169, 179–182). | Hai chỗ này cần nhánh sang WAITING_LOGIN và giữ file tạm. |
| `download_media_file.resolve_file` | Identity là `{"kind":"file","url": stable_url(url),"size"}`. `stable_url` bỏ tham số query kiểu chữ ký nhưng giữ nguyên đường dẫn. | Vé có token **trong đường dẫn** sẽ đổi identity mỗi lần lấy vé, dẫn tới `SOURCE_CHANGED` ở `_fresh_source`. `FileTransfer` cũng xóa `media.part` khi `identity_key` khác (dòng 172–175). Vì vậy adapter phải tự đặt identity theo nguồn + ID file ổn định + biến thể + kích thước, không theo URL vé. |
| `FileTransfer.run` | Gặp 403 thì lấy lại nguồn một lần (`refresh` = `_fresh_source`), đếm lại khi file nhích thêm. Validator là ETag mạnh, nếu không có thì Last-Modified, lấy từ lần đầu. (Sửa sau review M0, đã đọc lại dòng 171–217.) Khi bắt đầu một lượt chạy (Tiếp tục sau dừng hay restart), phần đã tải mà không có validator bị tải lại từ đầu (dòng 181–185). Nhưng **trong cùng một lượt**, sau lỗi mạng hoặc sau khi lấy nguồn mới vì 403, `_receive` vẫn gửi `Range`; nó chỉ thêm `If-Range` khi có validator. Không có validator thì máy chủ trả 206 và phần mới được nối vào. Code chỉ so byte đầu của `Content-Range` và tổng dung lượng, không chứng minh được cùng phiên bản file. Có validator mà máy chủ trả 200 thì tải lại từ 0. | `refresh` sẽ là "xin vé mới". **M3 phải chứng minh vé mới trỏ đúng phiên bản file đã tải** (validator khớp, hoặc bằng chứng mạnh tương đương của nguồn) rồi mới nối `media.part`. Chưa đủ bằng chứng thì không nối mù: tải lại từ đầu hoặc dừng với lỗi rõ ràng. |
| MKV | `CONTAINER_SUFFIX`: matroska → `.mkv`; chỉ MPEG-TS được chép sang MP4. `VIDEO_EXTENSIONS` (watcher, import, `verify_video`) có `.mkv`. | Hiện file MKV vào `input` với đuôi `.mkv`. Remux sang MP4 là code mới (M3). |
| `download_store` | `STATES` có 14 trạng thái, cột `state` là TEXT, không có CHECK. `SLOT_STATES` = PROBING, WAITING_SPACE, DOWNLOADING, VERIFYING, PUBLISHING, CANCELLING. Hàng đợi lấy `ORDER BY queued_at, id`. Cột mới thêm bằng `_ADDED_COLUMNS` + `ALTER TABLE`. | Thêm WAITING_LOGIN không cần migration giá trị. `resume` và `retry` đặt lại `queued_at` (đưa về cuối hàng) nên lúc đánh thức sau đăng nhập phải **giữ** `queued_at` để đúng thứ tự vốn có. |
| `download_worker` | `CANCELLABLE` và `RENAMABLE` tính bằng phép trừ nên tự gồm trạng thái mới; `STOPPABLE`, `RESUMABLE`, `RETRYABLE` ghi rõ từng trạng thái. `remove` chỉ cho `FINAL_STATES`. `dispatch` chỉ nhận QUEUED, đếm slot bằng `SLOT_STATES`. `recover`/`_reconcile` chỉ xét `SLOT_STATES`. Không có pause riêng cho bộ tải. `_lock` đã bao vài việc đọc/ghi file. | WAITING_LOGIN tự sống qua restart. Việc trình duyệt (đăng nhập, đọc danh sách, lấy vé) không được chạy dưới `_lock` hay khóa của store. |
| API và điện thoại | Mọi POST `/api/downloads*` nằm trong `phone_access.PHONE_ALLOWED_POSTS`. Mẫu chỉ-PC: `/api/tailscale*` trả 403 `pc_only` khi không phải loopback (`control_center.py` 2087–2092, 2292–2297), cộng `phone_access.PC_ONLY_POSTS`. | Route đăng nhập/ngắt kết nối dự kiến theo mẫu Tailscale. |
| Dashboard V2 | `download-core.js` chép lại `STATES`, nhãn, tông màu, nhóm, nút. `verify-download.cjs` ghi cứng 14 trạng thái. `test_dashboard_v2_downloads.py` so tập trạng thái của JS với backend. `download-view.js:45` ghi "Không dùng cookie hay đăng nhập". | M5 phải sửa đủ các chỗ này cùng lúc, nếu không gate Node và test so tập sẽ hỏng. |
| Lời nhắn "không tài khoản" | `download_sources.py:17–19`, `download_source_types.py:14–15`, `download_http.py:181`, `download_probe.py:47–53`, `download-view.js:45`. | Giữ đúng cho đường ẩn danh; khi triển khai chỉ thêm ngoại lệ cho nguồn tài khoản. M0 không sửa code. |
| ACL (đọc bằng `icacls`, không sửa) | `E:\DungChung\BiliFlow` và `state\` kế thừa từ ổ E: Authenticated Users có Modify, Users có RX, nhóm `CodexSandboxUsers` có Modify, và một SID không phân giải được cũng có Modify. | Thư mục phiên phải có ACL riêng, tắt kế thừa. DPAPI theo người dùng (`CurrentUser`) giữ nội dung khỏi tài khoản Windows khác, kể cả tài khoản sandbox; không giữ được khỏi chương trình chạy cùng tài khoản. |
| Playwright 1.63.0 trong `.venv` (đọc mã, chưa chạy) | Có `BrowserContext.storage_state(indexed_db=…)`, `new_context(storage_state=<dict hoặc đường dẫn>)` và tùy chọn `proxy` khi launch hoặc tạo context. | Có thể giữ phiên mà không ghi JSON thô ra đĩa và không thêm dependency. Phải kiểm bằng fixture ở M2. |

### 9.2 Mô-đun đề xuất

File mới dự kiến (mỗi file dưới 800 dòng, tên `download_*` để nằm ngoài khóa cache quét; test `test_download_modules_and_config_stay_out_of_the_scan_cache_key` giữ điều này):

| File (dự kiến) | Mục | Việc |
|---|---|---|
| `download_account_config.py` | M1, **đã có** | Đọc `config/download_accounts.local.json` (đã thêm vào `.gitignore`) và mẫu `config/download_accounts.example.json` chỉ có host `.example` (chi tiết bên dưới bảng). |
| `download_account_winsec.py` | M1, **đã có** | Phần Windows qua `ctypes`, không thêm dependency: DPAPI (`CryptProtectData`/`CryptUnprotectData`, chỉ cờ `CRYPTPROTECT_UI_FORBIDDEN`), SID của tài khoản đang chạy, tạo thư mục với ACL riêng, đặt lại ACL và đọc lại DACL cùng chủ sở hữu để kiểm. |
| `download_account_vault.py` | M1, **đã có** | Kho phiên: mã hóa DPAPI với entropy gồm id nguồn, ghi nguyên tử (`.tmp`, fsync, kiểm ACL, rồi `os.replace`, thử lại ngắn khi file bị giữ) vào `state\source-accounts\<SID tài khoản Windows>\<source_id>\session-<generation>.bin` (mỗi tài khoản Windows một thư mục riêng; mỗi generation một file, nên ghi hỏng không mất phiên cũ). Từ chối link, junction và root ngoài thư mục cài đặt hay `temp` của nó. Chỉ xóa đúng tên file của chính kho (`session-<n>.bin` và `.tmp` dở dang của nó) trong thư mục của nguồn. Bản giải mã chỉ nằm trong bộ nhớ. |
| `download_account_store.py` | M1, **đã có** | Bảng `source_account_state` trong `state\downloads.sqlite3` (tạo khi mở), một dòng mỗi tài khoản Windows và nguồn, khóa chính `(account_sid, source_id)`: `session_state` (NONE/ACTIVE/INVALID), `generation`, `authenticated_at`, `login_attempt`, `login_started_at`, `check_state`, `checked_at`, `error_code`, `updated_at`. Không có cột chứa bí mật. Mọi thay đổi là compare-and-set theo giá trị mong đợi; lỗi SQLite thì rollback ngay. |
| `download_accounts.py` | M1, **đã có** | `AccountManager`: trạng thái công khai, lượt đăng nhập, generation, TTL 3.600 giây không kéo dài, ngắt kết nối, phục hồi lúc khởi động và giao diện nội bộ cho M2/M4 (mục 9.8). Chỉ lưu cookie và storage của host nguồn (`own_state`). Mỗi nguồn một khóa. Lời nhắn là chữ cố định theo mã lỗi; cột `message` đề xuất ở M0 không cần. |
| `download_provider_config.py` | M3, **đã có** | Mô-đun lá tách từ `download_sources`: `PROVIDER_ID`, đọc `download_providers.local.json`, helper hiển thị, `PUBLIC_PROVIDER_IDS` (id provider công khai; test giữ khớp code). Cắt import vòng giữa registry và cấu hình tài khoản. |
| `download_account_edge.py` | M3, **đã có** (tách từ browser) | Phần Edge của trình duyệt có phiên: cờ khởi chạy, tùy chọn profile, môi trường, hố đen proxy, tìm tiến trình của đúng profile. |
| `download_account_runs.py` | M3, **đã có** | `run_with_session` (chuyển từ browser): khóa riêng mỗi root + SID + nguồn, một hạn chung, khi treo kết thúc Edge rồi driver của đúng lượt (mục 9.13), lưu cookie xoay vòng theo lease (mục 9.12). |
| `download_account_driver.py` | Sửa treo M3, **đã có** | Driver Playwright của đúng lượt: ghi khi runtime của lượt sinh ra nó (pid, thời điểm tạo, exe, là con của BiliFlow), kiểm lại rồi chỉ kết thúc qua handle của nó; chỗ lượt đang chờ chỉ dạng `module:hàm:dòng` (mục 9.13). |
| `download_account_listing.py` | M3, **đã có** | Danh sách phim/mùa/tập/bản, thứ tự, danh tính, `public()`, `plan_selection`, `film_choices` (mục 9.12). Không trình duyệt, không mạng. |
| `download_account_pages.py` | M3, **đã có** | Giao thức bộ đọc trang (`SourcePageReader`, `PAGE_READERS` trống), đọc danh sách qua các trang, lấy vé đúng một file (mục 9.12). |
| `download_account_browser.py` | M2a **đã có** lớp mạng (mục 9.9); M2b, M3 | Mở Edge qua Playwright: headed chỉ từ thao tác đăng nhập (M2b), headless cho kiểm tra phiên, đọc danh sách và lấy vé. Profile tạm cho mỗi lượt, xóa khi xong. Context nạp phiên từ dict trong bộ nhớ. Popup được phân loại theo host (tab vé của adapter hay quảng cáo, M3). Media và tải xuống trong trình duyệt bị chặn; file do `SafeHttp`/`FileTransfer` tải. |
| `download_account_login.py` | M2b, **đã có** | Điều phối cửa sổ đăng nhập (mục 9.11): `LoginCoordinator.start` là đường duy nhất tới trình duyệt có giao diện (`HeadedPermit`), chạy ở luồng riêng không giữ khóa, một hạn chung tối đa 10 phút, hủy được. Bộ xác nhận của adapter (`LOGIN_VERIFIERS`, theo id adapter trong code) đọc bằng chứng qua `LoginView` (URL, chữ của trang, fetch tới host của nguồn qua lớp mạng M2a). Có bằng chứng thì trạng thái trong bộ nhớ đi vào `complete_login`. Chưa nối route (M4). |
| `download_account_http.py` | M2a, **đã có** | Client của trình duyệt có phiên: mỗi lần gọi là một lượt trao đổi đã kiểm (https, đúng host của nguồn, địa chỉ công khai với DNS ghim, TLS, hạn giờ và giới hạn byte cho từng yêu cầu), không đi theo redirect, không tự thêm cookie (mục 9.9). |
| `download_account_proxy.py` | Không làm | Phương án A ở 9.4 không đạt yêu cầu (mục 9.9). |
| `download_account_sources.py` | M3, **đã có** | `AccountSourceProvider` theo `SourceProvider`, một provider mỗi nguồn (id = id nguồn). `claims` mọi host của nguồn (portal, tickets, files), để link của nguồn không bao giờ sang yt-dlp; chỉ trang phim trên portal được đọc. `resolve`: kiểm phiên, đọc danh sách, lấy vé, thăm dò file (mục 9.12). |

Cấu hình dự kiến của `download_account_config.py`:
- Dạng file: `sources: {<source_id>: {adapter, label, login_url, hosts: {portal: […], tickets: […], files: […]}, session_check}}`.
- Kiểm tra:
  - `source_id` theo `PROVIDER_ID`;
  - `adapter` phải có trong code;
  - host kiểm bằng `check_host`;
  - `login_url` phải là https, host thuộc `portal`;
  - không nhận lệnh, module hay JavaScript;
  - một host chỉ thuộc một nguồn và không được trùng `download_providers.local.json`; trùng thì bỏ cả hai và báo lỗi cấu hình như cách hiện có (không lộ phần link).

Các bước `resolve` dự kiến của `AccountSourceProvider`:
1. Thiếu phiên hoặc phiên hết hạn: báo lỗi cần đăng nhập.
2. Đọc danh sách file; nhiều mục thì `SourceNeedsChoice` có sẵn.
3. Lấy vé, bắt link Tải xuống.
4. `resolve_file` qua `SafeHttp` (không cookie), với identity của adapter thay cho identity theo URL.

Sửa dự kiến:
- `download_source_types.py`: lỗi `SourceLoginRequired` (mã `LOGIN_REQUIRED`, mang `source_id`, `generation`); `ResolveContext` thêm chỗ cho phiên chỉ adapter tài khoản đọc.
- `download_store.py`: thêm `WAITING_LOGIN` vào `STATES` (không vào `SLOT_STATES`, `FINAL_STATES`, `CLOSED_STATES`), cột `waiting_source` để đánh thức đúng nguồn. (Bảng tài khoản không nằm ở đây: M1 đã có nó trong `download_account_store.py`.)
- `download_worker.py`, `download_source_steps.py`: bắt `SourceLoginRequired` ở PROBING và DOWNLOADING → WAITING_LOGIN (giữ file tạm, nhả slot). Trước khi nhận một lượt QUEUED của nguồn đã biết là thiếu phiên thì chuyển thẳng WAITING_LOGIN, không chiếm slot, không lặp. Đánh thức: WAITING_LOGIN của đúng nguồn → QUEUED giữ `queued_at`, sau đăng nhập thành công và lúc khởi động (nếu phiên còn hiệu lực, phòng trường hợp tắt máy giữa lúc ghi phiên và lúc đánh thức).
- `download_api.py`, `control_center.py`, `phone_access.py`: thêm `accounts` vào snapshot và route chỉ-PC (mục 9.5).
- `dashboard_v2/download-core.js`, `download-view.js`, `download-live.js`, `theme.css`, `verify-download.cjs`, `download-fake-server.cjs`, các test V2: panel, trạng thái, nhóm, bộ đếm, CTA.
- `.gitignore`: thêm `config/download_accounts.local.json`. File này không nằm trong khóa cache quét.

### 9.3 Luồng dự kiến

1. **Đăng nhập (PC).**
   1. Người dùng chọn nguồn và bấm Đăng nhập. Server ghi trạng thái LOGGING_IN cho nguồn đó (nếu nguồn đang có thao tác khác thì trả 409).
   2. Luồng nền mở Edge headed với profile tạm và kiểm tra mạng (9.4), rồi mở `login_url`. Người dùng tự đăng nhập.
   3. Adapter chờ bằng chứng đăng nhập từ trang hay endpoint của nguồn, tối đa một thời hạn chờ riêng (10 phút, `LOGIN_ATTEMPT_MAX_SECONDS` ở M1; không liên quan TTL).
   4. Thành công:
      - `storage_state()` → DPAPI → ghi nguyên tử;
      - DB: CONNECTED, `generation` + 1, `authenticated_at` = lúc thành công;
      - đóng trình duyệt, xóa profile tạm;
      - đánh thức tác vụ WAITING_LOGIN của nguồn đó.
   5. Hủy, đóng cửa sổ hay hết thời hạn chờ: trở về trạng thái trước, không ghi phiên, không báo thành công. Lỗi ghi phiên: báo "Không lưu được phiên", không coi là đã kết nối.
2. **Dán link trang phim.**
   1. Registry khớp đúng host portal → `AccountSourceProvider`.
   2. Thiếu phiên hoặc phiên hết hạn: WAITING_LOGIN, không chiếm slot.
   3. Có phiên: PROBING (giữ slot). Context headless nạp phiên, đọc vùng bản tải và bỏ trailer, quảng cáo.
   4. Nhiều tập hay chất lượng: NEEDS_CHOICE (nhả slot). Phim lẻ: người dùng chọn bản → QUEUED → PROBING với lựa chọn đó. Phim nhiều tập: hộp chọn tập, rồi mỗi tập thành một tác vụ trong một nhóm (mục 9.10).
   5. Lấy vé, bắt đúng link Tải xuống, GET Range có giới hạn (`resolve_file`), identity của adapter → WAITING_SPACE.
3. **Tải.**
   1. DOWNLOADING: `_fresh_source` xin vé mới và so identity, rồi `FileTransfer` tải qua `SafeHttp` không cookie.
   2. Gặp 403: lấy vé mới một lần (cơ chế có sẵn).
   3. Lúc xin vé mà phiên đã hết: WAITING_LOGIN, giữ `media.part`. Đăng nhập lại → QUEUED (giữ thứ tự) → xin vé mới → so identity → tải nối.
4. **Phiên hết hạn khi file đang tải:** không ngắt. Chỉ lần xin vé kế tiếp mới kiểm phiên.
5. **Ngắt kết nối (PC):**
   - khóa việc xin vé mới, `generation` + 1;
   - xóa đúng các file `session-<generation>.bin` (và `.tmp` dở dang của chúng) của nguồn sau khi kiểm đường dẫn;
   - tác vụ đang tải bằng link đã có vẫn chạy; tác vụ cần vé mới chuyển WAITING_LOGIN.

### 9.4 Quyết định kỹ thuật cần chốt ở M2 (đề xuất)

**Đã chốt ở M2a (2026-10-08): B, không tự làm cookie jar (cookie nằm trong jar của chính Edge). A không đạt yêu cầu kiểm từng redirect và giới hạn từng yêu cầu. Lý do và bằng chứng ở mục 9.9.** Phần dưới đây là đề xuất của M0, giữ nguyên để đối chiếu.

Mạng của trình duyệt có phiên:
- **A (đề xuất): proxy kiểm tra cục bộ.**
  - Ưu điểm: trình duyệt tự giữ cookie đúng chuẩn (domain/path/secure/SameSite, HttpOnly) và TLS đi thẳng tới máy chủ. Mỗi kết nối mới, kể cả kết nối tới đích redirect, phải qua bước kiểm host, địa chỉ công khai và DNS ghim.
  - Giới hạn: không đọc được từng redirect bên trong TLS. Chỉ giới hạn được byte và thời gian theo kết nối, không theo từng phản hồi.
  - Phải chặn QUIC và WebRTC đi ngoài proxy, và kiểm việc phân giải DNS không lộ ra ngoài proxy.
  - (Sửa sau review M0) AGENTS.md vẫn đòi checked redirects và bounded reads cho mọi yêu cầu của trình duyệt có phiên; **không sửa giảm yêu cầu đó để chấp nhận một proxy chưa kiểm chứng**. A chỉ được chọn khi M2 chứng minh bằng fixture rằng nó đáp ứng đúng các yêu cầu này. Nếu không chứng minh được thì dùng B, hoặc dừng lại hỏi người dùng.
- **B: giữ `route.fulfill` qua `SafeHttp` như `embedded-media`, thêm một cookie jar riêng cho nguồn.**
  - Ưu điểm: dùng lại đúng các kiểm tra hiện có.
  - Nhược điểm: phải phục vụ mọi loại tài nguyên của trang đăng nhập (ảnh, font, script bên thứ ba, khung thử thách). BiliFlow phải tự làm lại luật cookie. Cookie đặt bằng JavaScript và các luồng đăng nhập phức tạp dễ hỏng.
- Phương án nào cũng vậy: yt-dlp không nhận phiên, provider ẩn danh không nhận phiên, `SafeHttp` vẫn không cookie.

Định dạng phiên:
- Dict của `storage_state()` (cookie, kể cả cookie phiên, cùng localStorage; IndexedDB chỉ khi cần) → JSON trong bộ nhớ → DPAPI → `session-<generation>.bin` (M1 đã có phần sau dict).
- Sau một lượt headless thành công, có thể ghi lại khi cookie đổi (xoay vòng), nhưng không đổi `authenticated_at` và không kéo dài TTL.

### 9.5 Trạng thái, API và giao diện dự kiến

Trạng thái tài khoản:

| Trạng thái | Nhãn | Ý nghĩa |
|---|---|---|
| NOT_CONNECTED | Chưa kết nối | Chưa có phiên. |
| LOGGING_IN | Đang đăng nhập | Cửa sổ đăng nhập đang mở. |
| CONNECTED | Đã kết nối | Có phiên còn hiệu lực. |
| NEEDS_LOGIN | Cần đăng nhập lại | Đã qua 3.600 giây (tính lúc đọc, không cần bộ hẹn giờ), có bằng chứng phiên hỏng, hoặc không giải mã được phiên. |
| CHECK_FAILED | Không kiểm tra được kết nối | Chỉ dùng cho adapter có bộ kiểm phiên thật, khi lỗi mạng lúc kiểm. Không biến phiên tốt thành hết hạn. |

Trạng thái tác vụ WAITING_LOGIN ("Chờ đăng nhập"):
- Không giữ slot.
- Tính vào giới hạn 100 lượt chưa xong. Link vẫn bị chặn trùng như lượt chưa xong.
- Thao tác:
  - Hủy, Đổi tên: tự có.
  - Dừng → STOPPED, giữ file tạm: đề xuất.
  - Thử lại, Xóa: không mở trực tiếp; Xóa sau khi Hủy.
- Nhóm "cần bạn xử lý" trên trang, có CTA "Đăng nhập <tên nguồn>".
- Sự kiện ghi id nguồn, không ghi bí mật.

API:
- Snapshot `GET /api/downloads` thêm `accounts`. Mỗi mục chỉ có: `id`, `label`, `state`, `authenticated_at`, `recheck_at`, `session_check` (`ttl` hay `live`) và `message` đã lọc. M1 đã có đúng dict này (`AccountManager.statuses()`), thêm `checked_at` và `error_code` (mã cố định); không có host, link đăng nhập hay giá trị phiên.
- `POST /api/download-accounts/<source_id>/(login|cancel-login|disconnect)`:
  - chỉ PC: loopback + `X-BiliFlow-Token`, Host như route hiện có;
  - thêm vào `phone_access.PC_ONLY_POSTS` kèm lý do;
  - `login` trả về ngay, việc mở cửa sổ chạy ở luồng riêng.
- Không có route nào trả cookie hay phiên.

Điện thoại:
- Chỉ xem trạng thái.
- Nút hiện hướng dẫn "làm trên PC" và không gửi lệnh mở cửa sổ.

### 9.6 Phân loại lỗi (ánh xạ vào code dự kiến)

| Hiện tượng | Kết quả |
|---|---|
| Adapter thấy trang đăng nhập, redirect về login hay thông báo phiên không hợp lệ rõ ràng | `SourceLoginRequired` → WAITING_LOGIN của đúng nguồn; hạ phiên nếu đúng `generation` |
| 401 hay 403 từ máy chủ file | Coi là vé hết hạn: lấy vé mới một lần. Chỉ khi lấy vé cho thấy cần đăng nhập mới sang WAITING_LOGIN |
| 407 (sửa sau review M0) | Lỗi xác thực **proxy**, không phải vé hết hạn, cũng không phải nguồn đòi đăng nhập. `SafeHttp` không dùng proxy, nên 407 là trả lời bất thường của mạng hay của máy chủ: báo lỗi mạng hoặc cấu hình, không xin vé mới, không sang WAITING_LOGIN. Nếu M2 thêm proxy cục bộ, 407 của proxy đó là lỗi của BiliFlow, cũng không quy thành đăng nhập nguồn. |
| 403 ở trang danh sách hay trang vé mà không có bằng chứng đăng nhập | Giữ `FORBIDDEN` (quyền, giới hạn tần suất hay anti-bot), không ép đăng nhập |
| NETWORK, SERVER_BUSY, DNS_FAILED | INTERRUPTED như hiện có; lúc kiểm phiên thật thì CHECK_FAILED; không mở cửa sổ |
| File không còn ở nguồn | `UNAVAILABLE` (FAILED) |
| Thiếu chỗ | WAITING_SPACE, TOO_LARGE, DISK_FULL như hiện có |
| Nguồn đổi | `SOURCE_CHANGED` như hiện có |

### 9.7 Điểm cần kiểm chứng ở các mục sau (chưa biết, không được coi là đã đúng)

M1:
- DPAPI qua `ctypes` chạy được dưới tài khoản đang chạy Start-BiliFlow. Với blob hỏng hoặc của tài khoản khác, nguồn sang NEEDS_LOGIN với lời nhắn "Không đọc được phiên đã lưu", không lỗi chương trình. Test bằng bộ mã hóa giả, cộng một lượt mã hóa/giải mã thật bằng dữ liệu giả.
- ACL riêng:
  - tắt kế thừa;
  - giữ tài khoản đang chạy BiliFlow, SYSTEM và Administrators (người dùng đã chọn), không giữ ai khác;
  - đọc lại DACL sau khi đặt;
  - từ chối link và junction.
- Không lộ bí mật: dùng một cookie mồi trong test, rồi tìm chuỗi đó trong DB, API, log và thư mục tạm.
- Đồng hồ giả: đúng mốc 3.600 giây; polling hay tác vụ chạy không kéo dài phiên. `generation` cũ không ghi đè phiên mới.
- `.gitignore` có file cấu hình mới. File mẫu chỉ có host `.example`.

M2:
- Chọn A hay B (9.4). Fixture phải chứng minh:
  - từ chối địa chỉ nội bộ, DNS rebinding và host ngoài danh sách;
  - cookie không đi sang host khác;
  - QUIC và WebRTC không đi ngoài proxy.
  - M2a (mục 9.9): đã chọn B; ba điểm trên đã kiểm bằng fixture trên Edge headless thật. QUIC chỉ kiểm gián tiếp (9.9, giới hạn).
- Phiên mang được từ headed sang headless qua `storage_state` (cookie phiên, localStorage, IndexedDB nếu cần). Kiểm bằng fixture trước, nguồn thật ở M7. M2a: cookie phiên và localStorage mang được giữa hai context headless. M2b: cookie, localStorage và IndexedDB (`storage_state(indexed_db=True)`, nạp lại bằng `set_storage_state`) mang được từ context đăng nhập sang lượt ẩn, kiểm trên Edge headless thật; từ cửa sổ có giao diện thật thì chưa kiểm (mục 9.11).
- Luồng đăng nhập thật cần những host nào (nhà cung cấp danh tính, khung thử thách, CDN): chưa biết. Chỉ xem cùng người dùng qua biên bản riêng ngoài repo; không commit.
- Bằng chứng đăng nhập của adapter. Đóng cửa sổ, hủy hay hết thời hạn chờ không bao giờ là thành công. M2b: có giao diện bộ xác nhận và một bộ xác nhận fixture; bằng chứng của nguồn thật chưa biết, nên `ticket-files` chưa có bộ xác nhận và đăng nhập bị từ chối (LOGIN_UNSUPPORTED) trước khi mở cửa sổ (M3/M7).
- Cửa sổ headed chỉ mở từ nút bấm; test dùng launcher giả. M2b: chỉ `LoginCoordinator.start` tạo được `HeadedPermit`; lượt ẩn không xin được cửa sổ (test). Nút bấm là route của M4.
- Edge headless với proxy và `new_context(storage_state=…)` trên Playwright 1.63.0: API có trong mã, chưa chạy. M2a: đã chạy, bằng `launch_persistent_context` (profile trên ổ E) và `set_storage_state`.

M3:
- Trang vé:
  - phân biệt tab vé với popup quảng cáo;
  - chờ nút theo luồng bình thường, không gọi endpoint tắt;
  - có thời hạn chờ.
- Máy chủ file:
  - có cần cookie không (khảo sát một lần: không);
  - vé mới có giữ ETag/Last-Modified không. Trong cùng một lượt, `FileTransfer` hiện nối bằng `Range` mà không có `If-Range` khi thiếu validator (9.1). M3 phải chứng minh cùng phiên bản file trước khi nối; chưa đủ bằng chứng thì không nối mù;
  - thời hạn và hạn mức vé, vé đồng thời của cùng tài khoản;
  - Range ở offset khác (khảo sát một lần: được);
  - HEAD trả HTML (khảo sát), nên không dùng HEAD.
- Một vé hay hai vé mỗi lượt: lượt chạy probe → chờ chỗ → tải trong cùng một luồng. Dùng lại vé của lúc thăm dò (chỉ trong bộ nhớ) khi còn hạn có thể bớt một lần xin vé.
- MKV: người dùng chọn giữ `.mkv` như cơ chế hiện có, chưa thêm remux. Vẫn nên kiểm bước quét và xuất trên một MKV tổng hợp.

M4:
- WAITING_LOGIN:
  - không giữ slot, không lặp xin vé;
  - đánh thức giữ đúng thứ tự (`queued_at`);
  - đúng khi hủy hay dừng chen ngang lúc đăng nhập;
  - restart giữ nguyên, rồi đánh thức lúc khởi động nếu phiên còn hiệu lực.
- Route chỉ-PC, GET trên điện thoại, token và Host.
- Không làm việc nặng dưới khóa.
- Pause riêng cho bộ tải: không làm (người dùng đã chọn ngày 2026-10-07).

M5:
- Panel và CTA đúng ở giao diện sáng và tối, trên PC và điện thoại.
- Thay chữ "Không dùng cookie hay đăng nhập".
- Sửa đồng thời `download-core.js`, `verify-download.cjs` và test so tập trạng thái.

M7:
- Người dùng tự đăng nhập trên Control Center thử (root tạm, cổng riêng).
- Thăm dò có giới hạn, không tải trọn phim.
- Mốc 1 giờ kiểm bằng đồng hồ giả.
- Phiên hết và link hết là hai test riêng.

Không đọc trong M0: biên bản riêng ngoài repo, cấu hình local và cơ sở dữ liệu của bản chính. Không gọi API nào của Control Center thật.

Các điểm cần kiểm chứng của yêu cầu chọn tập (M3–M7) nằm ở mục 9.10.

### 9.8 Kết quả M1 (2026-10-07)

Phạm vi: thư viện và test, chỉ trong worktree. Không có trình duyệt, proxy, provider, hàng đợi, API hay giao diện (M2–M5). Không file nào khác import các mô-đun mới. Không chạy Control Center nào, không đọc cấu hình local hay DB của bản chính.

File mới:
- `src/biliflow/download_account_config.py`, `download_account_winsec.py`, `download_account_vault.py`, `download_account_store.py`, `download_accounts.py`;
- `config/download_accounts.example.json` (chỉ host `.example`);
- `tests/test_download_account_config.py`, `tests/test_download_account_vault.py`, `tests/test_download_accounts.py`.

File sửa: `.gitignore` (thêm `config/download_accounts.local.json`), `AGENTS.md`, `docs/VIDEO_DOWNLOAD_PLAN.md` và tài liệu này.

**Cấu hình** (`read_account_config`):
- Khóa chặt chẽ; khóa bắt đầu bằng `_` là ghi chú.
- Id theo `PROVIDER_ID`, không trùng id provider ẩn danh, không phải tên thiết bị Windows giữ riêng (`con`, `nul`, `com0`–`com9`…). Adapter phải có trong code (`ACCOUNT_ADAPTERS`; hiện có `ticket-files`, `session_check = "ttl"`, provider của nó là việc M3).
- Nhãn 1–60 ký tự, không ký tự điều khiển. Host là tên trần, kiểm bằng `check_host`, theo ba vai `portal` (bắt buộc), `tickets`, `files`.
- `login_url` qua `check_link`, phải https, không có `#`, host thuộc `portal`.
- Một host thuộc hai nguồn thì bỏ cả hai. Host trùng `download_providers.local.json` thì bỏ nguồn, provider giữ host.
- Lời báo lỗi chỉ ghi host, không ghi đường dẫn hay query của link. `repr` của nguồn không có host hay link.

**Kho phiên** (`SessionVault`):
- File `state\source-accounts\<SID tài khoản Windows>\<source_id>\session-<generation>.bin` = `BFSESSION1\n` + blob DPAPI của JSON `{v, source, generation, authenticated_at, state}`. Entropy gồm id nguồn. Khi đọc, nguồn, generation và `authenticated_at` trong bản giải mã phải khớp tên file và dòng DB; file bị chép sang nguồn hay generation khác bị coi là hỏng.
- Mỗi tài khoản Windows một thư mục riêng (sửa sau review). Trước đó, cùng một thư mục cho mọi tài khoản sẽ khiến tài khoản thứ hai không bao giờ lưu được phiên. Dòng DB cũng theo tài khoản (xem **Bảng tài khoản**), nên tài khoản khác bắt đầu ở NOT_CONNECTED và không đụng thư mục hay trạng thái của tài khoản đầu.
- SID là SID của token tiến trình (`current_user_sid`, đọc qua `PrivateFolderAcl` của kho), không bao giờ lấy từ cấu hình hay request. Cấu hình có khóa `account_sid` bị từ chối như mọi khóa lạ.
- DPAPI cho người dùng hiện tại, không `CRYPTPROTECT_LOCAL_MACHINE`. Bộ đệm bản rõ trong `ctypes` được ghi đè bằng 0 sau khi gọi API.
- Thư mục của tài khoản được tạo kèm ACL chỉ có ba SID: tài khoản đang chạy BiliFlow, SYSTEM, Administrators; tắt kế thừa. Thư mục nguồn và file kế thừa từ đó. `state\source-accounts` chỉ là thư mục thường chứa các thư mục tài khoản.
- Mỗi lần ghi và đọc, kho đọc lại DACL và chủ sở hữu. Có SID khác, ACE loại lạ, kế thừa bật hay chủ sở hữu lạ thì từ chối (`SESSION_REFUSED`). Kho chỉ đặt lại ACL cho thư mục tài khoản của chính nó, khi chủ sở hữu là một trong ba SID và không có ACE lạ; không bao giờ đổi quyền của `state\` hay thư mục cài đặt.
- Ghi: `.tmp` tên ngẫu nhiên → fsync → kiểm ACL của file → `os.replace`. `os.replace` được thử lại tối đa 3 lần (chờ 0,1 rồi 0,2 giây) khi Windows báo file đang bị giữ, ví dụ do phần mềm diệt virus. Lỗi ở bất kỳ bước nào thì xóa `.tmp`; file cũ giữ nguyên.
- Từ chối:
  - link tượng trưng và junction ở `state`, `source-accounts`, thư mục tài khoản, thư mục nguồn và file;
  - root không phải thư mục cài đặt hay thư mục trong `temp` của nó (`job_purge.allowed_project_root`);
  - id hay generation sai dạng, tên thiết bị Windows, SID sai dạng; phiên lớn hơn 4 MiB.
- Phân loại lỗi:
  - không giải mã được, sai dạng, sai nguồn hay generation → `SESSION_CORRUPT`;
  - không có file → `SESSION_MISSING`;
  - lỗi quyền hay ổ đĩa → `SESSION_IO_ERROR`;
  - ACL hay link sai → `SESSION_REFUSED`.
- Kho không bao giờ xóa file vì lỗi đọc. Lệnh xóa chỉ xóa `session-<n>.bin` và `.tmp` dở dang của chúng, không xóa tên khác.

**Bảng tài khoản** (`AccountStore`):
- Schema (sửa sau review M1 của Codex): bảng `source_account_state` trong `state\downloads.sqlite3`, tạo bằng `CREATE TABLE IF NOT EXISTS` khi mở. Cột: `account_sid`, `source_id`, `session_state` (NONE/ACTIVE/INVALID), `generation`, `authenticated_at`, `login_attempt`, `login_started_at`, `check_state`, `checked_at`, `error_code`, `updated_at`. Khóa chính `(account_sid, source_id)`. Không có cột chứa bí mật.
- Một `AccountStore` mở cho đúng một SID: mọi `get`, `ensure` và compare-and-set đều lọc `account_sid = ? AND source_id = ?`. `account_sid` không nằm trong các cột được đổi, nên không dòng nào chuyển sang tài khoản khác. Store từ chối SID sai dạng. Manager từ chối store của SID khác với kho.
- Migration: không chuyển dữ liệu. Bảng `source_accounts` của bản nháp M1 (khóa chỉ theo `source_id`) chưa từng chạy ngoài root tạm của test, và dòng của nó không gán được cho tài khoản Windows nào. Nếu một DB còn bảng đó, nó không được đọc, sửa hay xóa. Các bảng `download_*` cùng file không bị đụng. Không dùng `PRAGMA user_version` (file này dùng chung với hàng đợi tải). Chỉ thử trên DB giả trong root tạm; không mở DB của bản chính.
- Lỗi SQLite ở lúc ghi hay commit thì rollback ngay (sửa sau review), nên một thay đổi commit hỏng không bao giờ bị commit ké ở lần gọi sau.

**Session manager** (`AccountManager`), giao diện nội bộ cho M2 và M4:
- Root được kiểm (`allowed_project_root`) trước khi tạo DB hay thư mục nào.
- Phạm vi: một manager làm việc cho đúng một tài khoản Windows (`account_sid` = SID của kho). Status, đăng nhập, hủy, ngắt kết nối, `session_for`, xoay vòng, kiểm tra, compare-and-set và `recover` chỉ thấy và đổi dòng cùng thư mục của tài khoản đó. Tài khoản B đọc, đăng nhập, ngắt kết nối hay khởi động lại không hạ, thay hay dọn phiên của A, và ngược lại, kể cả khi chạy lần lượt A → B → A.
- `begin_login(source)` → `LoginAttempt` (id ngẫu nhiên, chỉ trong DB và bộ nhớ). LOGGING_IN kéo dài tối đa 10 phút. Trong lúc đó phiên cũ (nếu có) vẫn dùng được.
- `complete_login(attempt, storage_state)` là chỗ M2 trao trạng thái đã xác thực (dict dạng `storage_state()` của Playwright); hàm này không mở trình duyệt hay gọi trang nào.
  - Chỉ giữ cookie mà trình duyệt sẽ gửi tới một host đã cấu hình của nguồn, và storage của origin https (cổng mặc định) thuộc các host đó (`own_state`, sửa sau review). Cookie hay origin của host khác (nhà cung cấp danh tính, quảng cáo, trang khác) bị bỏ, không lưu.
  - Ghi file của generation mới trước, rồi compare-and-set dòng DB theo `login_attempt` và `generation`.
  - DB lỗi thì xóa file mới và báo `SESSION_SAVE_FAILED`. Thành công thì xóa file của các generation khác.
  - Lượt đã hủy, bị thay, hết hạn hay bị ngắt kết nối: `StaleLogin`, không ghi gì.
- `end_login(attempt, code)` cho hủy, đóng cửa sổ, hết hạn hay thất bại; không mã nào là thành công.
- `cancel_login(source)` và `disconnect(source)` là nút của người dùng; cả hai kết thúc lượt đăng nhập đang mở.
  - `disconnect` tăng generation, về NONE, rồi mới xóa file của nguồn.
  - Nếu compare-and-set của dòng không thành (chỉ khi có nơi khác ghi cùng dòng), `disconnect` báo `ACCOUNT_BUSY` và không xóa file nào.
- Phiên đã INVALID giữ lý do cần đăng nhập (ví dụ `SESSION_REJECTED`) qua một lượt đăng nhập bị hủy hay đóng cửa sổ; mã của lượt đó không đè lý do này.
- `session_for(source)` trả về một trong ba:
  - `SessionLease` (generation, `authenticated_at`, state; chỉ trong bộ nhớ);
  - `LoginRequired`: chưa kết nối, quá 3.600 giây, đồng hồ lùi trước lúc đăng nhập, nguồn từ chối phiên, file hỏng hay mất;
  - `SessionUnavailable`: quyền, ổ đĩa hay ACL. Phiên giữ nguyên, không phải lỗi đăng nhập; lần đọc tốt kế tiếp xóa mã lỗi.
- `save_rotated(lease, state)` ghi cookie đã xoay vòng (cũng qua `own_state`) với cùng generation và cùng `authenticated_at`.
- `mark_invalid` và `record_check(valid | invalid | unreachable)` bỏ qua generation khác. `unreachable` chỉ cho CHECK_FAILED ở adapter `live` và không hạ phiên; với adapter `ttl` nó bị bỏ qua, TTL quyết định.
- `recover()` lúc khởi động:
  - kết thúc mọi lượt đăng nhập dở (`LOGIN_INTERRUPTED`);
  - phiên ACTIVE mất file → NEEDS_LOGIN (`SESSION_MISSING`); lỗi đọc thì giữ phiên kèm mã lỗi;
  - xóa file của generation khác và `.tmp` dở dang; xóa xong file mà lần ngắt kết nối trước chưa xóa được thì bỏ mã `SESSION_REMOVE_FAILED`;
  - lỗi DB ở một nguồn không chặn các nguồn khác; trả về id các nguồn chưa xử lý xong để M4 ghi log.
- Mọi manager của cùng root và cùng tài khoản trong một tiến trình dùng chung khóa từng nguồn. `close()` đóng DB.
- TTL: dùng được khi 0 ≤ bây giờ − `authenticated_at` < 3.600 giây, tính lúc đọc, không cần bộ hẹn giờ. Đọc trạng thái, kiểm tra phiên, xoay vòng cookie và khởi động lại không ghi `authenticated_at`. Adapter kiểu `live` (chưa có adapter thật nào) không dùng TTL.

**Review sau khi viết code** (hai agent review, chỉ đọc, 2026-10-07): không có lỗi CRITICAL hay HIGH.
- Đã sửa:
  - MEDIUM: bảng thiếu rollback;
  - MEDIUM: lưu cả cookie và origin của host khác;
  - MEDIUM: tài khoản Windows thứ hai bị kẹt;
  - LOW:
    - `disconnect` bỏ qua compare-and-set;
    - CHECK_FAILED ở adapter `ttl`;
    - mã lỗi cũ còn lại (`SESSION_REMOVE_FAILED`, lý do INVALID bị đè);
    - tên thiết bị Windows;
    - root được kiểm sau khi đã tạo DB;
    - không thử lại khi file bị giữ;
    - `recover` dừng ở lỗi DB đầu tiên;
    - `assert` trong `session_for`;
    - docstring của `save_rotated` và `end_login`;
    - khóa không dùng chung giữa hai manager;
    - thiếu `close()`.
- Không sửa, ghi vào giới hạn bên dưới: lỗi DPAPI ngoài danh sách lỗi môi trường; dữ liệu của nguồn đã bỏ khỏi cấu hình; import helper riêng của `download_sources`.
- Review M1 của Codex (P2, 2026-10-07, `BILIFLOW-SOURCE-ACCOUNTS-M1-REVIEW.md`, ghi chú riêng bên ngoài repo): kho phiên chia theo SID nhưng dòng DB chỉ theo `source_id`. Ba regression của Codex: B đọc làm A thành INVALID; B đăng nhập làm generation chung đổi nên A mất phiên; A `recover` sau đó xóa file tốt của A. Đã sửa bằng bảng `source_account_state` theo `(account_sid, source_id)` (xem **Bảng tài khoản**).
- Đã thêm các test còn thiếu mà review chỉ ra, và kiểm rằng test commit hỏng và test lọc host **thất bại** khi đặt lại hành vi cũ trong bộ nhớ (3/3 thất bại), rồi đạt với code mới.

**Bằng chứng.** Chạy trong worktree bằng `.venv` của bản chính, `PYTHONPATH` trỏ vào worktree. Mọi root là thư mục tạm dưới `E:\DungChung\BiliFlow\temp`, dữ liệu tự tạo.
- `tests.test_download_account_config`: 11 test, OK (thêm ba tên thiết bị Windows và khóa `account_sid` vào các mục sai).
- `tests.test_download_account_vault`: 19 test, OK. Dùng DPAPI và ACL thật với dữ liệu giả:
  - DACL của thư mục tài khoản đúng ba SID, tắt kế thừa, không còn `.tmp`;
  - cookie mồi không có trong byte của file;
  - blob bị sửa hay entropy sai bị từ chối;
  - junction ở bốn cấp và symlink bị từ chối; root ngoài bị từ chối;
  - thử lại khi file bị giữ, rồi bỏ cuộc sau 3 lần mà file cũ còn nguyên;
  - hai tài khoản Windows, hai thư mục; SID sai dạng bị từ chối trước khi tạo thư mục.
- `tests.test_download_account_scope` (mới, sau review của Codex): 12 test, OK:
  - ba regression của Codex, nguyên nội dung; riêng test đầu: mã của B là `NOT_CONNECTED` (B chưa từng đăng nhập trong phạm vi của B), không phải `SESSION_MISSING` như bản gốc giả định từ dòng chung;
  - A → B → A → B với khởi động lại: mỗi bên giữ generation, file và `authenticated_at` của mình; A vẫn dùng được phiên sau khi khởi động lại nếu chưa hết TTL; B hết giờ không làm A hết giờ;
  - mọi thao tác của B (đăng nhập, đóng cửa sổ, `mark_invalid`, `record_check`, `save_rotated` bằng lease của A, ngắt kết nối, `recover`) không đổi trạng thái hay file của A; ngắt kết nối của A không đụng B;
  - mỗi tài khoản một dòng và một khóa; manager từ chối store của SID khác;
  - store: cột lạ (kể cả `account_sid`) và trạng thái lạ bị từ chối, SID sai dạng bị từ chối trước khi tạo DB, hai SID chỉ đổi dòng của mình;
  - migration trên DB giả: bảng `source_accounts` của bản nháp và một bảng `download_*` giữ nguyên dòng; bảng mới có khóa chính `(account_sid, source_id)`.
- `tests.test_download_accounts`: 44 test, OK (hai test chuyển sang file trên):
  - mốc 3.599,999 / 3.600 / 3.601 giây; đọc, kiểm tra, xoay vòng và khởi động lại không kéo dài phiên;
  - khởi động lại; hai nguồn độc lập; lưu của nguồn này không chặn nguồn khác hay trạng thái (test hai luồng); hai lượt đăng nhập bắt đầu cùng lúc chỉ ra một;
  - hủy hay ngắt kết nối chen ngang callback, kể cả một test hai luồng;
  - kho hỏng, file mất, thư mục bị từ chối, lỗi ghi, lỗi DB sau khi ghi, commit hỏng, sai generation, xóa lỗi lúc ngắt kết nối rồi dọn lúc khởi động;
  - chỉ giữ cookie và origin của host nguồn; tài khoản Windows khác; root ngoài bị từ chối trước khi tạo gì;
  - cookie mồi với DPAPI thật: chuỗi mồi không có trong DB (kể cả `-wal`), trạng thái, `repr`, lời lỗi, log hay file nào dưới root tạm; `account_sid` của manager khớp SID thật của tiến trình và không có trong trạng thái công khai.
- Toàn bộ `test_download*.py` với `BILIFLOW_FFMPEG` trỏ tới FFmpeg của bản chính (worktree không có `tools\ffmpeg`): 539 test, OK, không skip, 161 giây.
- File regression gốc của Codex, chạy nguyên văn sau khi sửa: 2/3 OK. Test đầu dừng ở phép so mã của B (`NOT_CONNECTED` thay cho `SESSION_MISSING`, xem trên), nên phép kiểm "A vẫn dùng được phiên" ngay sau đó không chạy trong file gốc; bản trong test suite chạy cả hai và đạt.
- Kiểm ngược: đặt lại dòng chung (mọi store dùng một SID) trong bộ nhớ thì 7/8 test phạm vi tài khoản thất bại; test còn lại kiểm việc từ chối store khác SID, không phụ thuộc dòng chung.
- `tests.test_cache_dependencies` và `tests.test_stage_cache`: 17 test, OK. `stage_source_paths` của cả 10 stage quét không chứa file `download_*` nào. Không đụng file nào trong khóa cache (`pyproject.toml`, `config/license_policy.json`, `scripts/env.ps1`, `cli.py`…).
- Không file nào ngoài các mô-đun mới import chúng, nên yt-dlp và provider ẩn danh không đổi.

**Giới hạn còn lại:**
- DPAPI theo người dùng chỉ giữ phiên khỏi tài khoản Windows khác. Chương trình chạy cùng tài khoản Windows vẫn giải mã được.
- Chưa thử bằng hai tài khoản Windows thật. Phạm vi theo tài khoản được kiểm bằng SID giả, ACL giả và bộ mã hóa giả; SID thật của tiến trình chỉ được kiểm ở test cookie mồi (khớp `current_user_sid`).
- Phần đầu blob DPAPI không được xác thực hết: lật thử từng byte thì vài byte đầu vẫn giải mã được. Nội dung vẫn được bảo vệ, và phong bì JSON được so lại nguồn, generation và thời điểm.
- Lỗi giải mã DPAPI ngoài danh sách lỗi môi trường (8, 14, 1450, 1722, 1723, 1726) được coi là phiên hỏng: nguồn sang NEEDS_LOGIN và cần đăng nhập lại, kể cả khi lỗi chỉ là tạm thời. File vẫn được giữ; không mất dữ liệu.
- Có khoảng hở TOCTOU giữa lúc kiểm link, ACL và lúc mở, ghi hay đổi tên file theo đường dẫn.
  - Bên trong thư mục tài khoản, ACL riêng chặn mọi tài khoản ngoài tài khoản đang chạy, SYSTEM và Administrators.
  - `state\` và `state\source-accounts\` vẫn mang quyền kế thừa của thư mục cài đặt (Authenticated Users có Modify). Một tài khoản Windows khác trên máy có thể đổi tên hay thay chúng bằng junction đúng trong khoảng hở đó.
  - Hậu quả: phiên bị từ chối hay coi là hỏng (cần đăng nhập lại), hoặc một blob đã mã hóa bị ghi vào chỗ họ chọn. Tài khoản đó không giải mã được blob (DPAPI theo người dùng). Chưa có kiểm tra theo handle (ví dụ `GetFinalPathNameByHandle`).
- Khóa từng nguồn là khóa trong tiến trình: giả định một Control Center trên một root, như hiện có. Hai tiến trình cùng root không được hỗ trợ; compare-and-set chỉ giảm thiệt hại.
- Dòng và file của một nguồn bị bỏ khỏi cấu hình được để nguyên (đã mã hóa, không dùng) cho tới khi nguồn được cấu hình lại. Tương tự, dòng và thư mục của một tài khoản Windows không còn dùng BiliFlow được để nguyên: tài khoản khác không đọc hay dọn chúng.
- `download_account_config.py` dùng hai helper riêng `_listing`, `_shown` của `download_sources`, và `RESERVED_IDS` đọc `SITE_PROVIDERS` lúc import. M3 cần tách `PROVIDER_ID` và các helper này sang một mô-đun lá trước khi đưa provider tài khoản vào registry, để tránh import vòng.
- Adapter `ticket-files` mới chỉ có khai báo, chưa có provider (M3). Chưa có bộ kiểm phiên thật (adapter `live`); `record_check` là chỗ để nó báo kết quả.
- Chưa có gì gọi `AccountManager`. Bảng `source_account_state` chỉ được tạo khi thư viện được gọi, hiện chỉ trong test trên root tạm.
- `.gitignore` đã có `state/`, nên thư mục kho và DB không vào Git.

### 9.9 Kết quả M2a (2026-10-07 → 2026-10-08): lớp mạng của trình duyệt có phiên

Phạm vi M2a: chỉ kiểm chứng và làm lớp mạng cho context trình duyệt có phiên. Chưa nối API, giao diện, hàng đợi hay provider lấy vé; chưa mở cửa sổ headed (M2b). Mọi kiểm tra dùng fixture tự tạo (domain `.example`, chứng chỉ của một CA thử tạo lúc chạy) trong root tạm dưới `temp\`. Không truy cập site thật, không mở hay giải mã phiên thật, không đọc cấu hình local, DB bản chính hay biên bản riêng.

**Đối chiếu A/B (9.4) và thiết kế chọn: B, đã chỉnh.**
- A (proxy CONNECT cục bộ, không giải TLS) **không đạt** yêu cầu:
  - proxy chỉ thấy `host:port` của tunnel; Location của redirect và thân phản hồi nằm trong TLS, nên không kiểm được từng redirect;
  - một tunnel chở nhiều yêu cầu (keep-alive, HTTP/2), nên giới hạn byte/thời gian chỉ đặt được cho cả tunnel, không cho từng yêu cầu;
  - muốn thấy từng yêu cầu thì phải giải TLS (MITM): cài CA vào trình duyệt và tự kiểm chứng chỉ thay trình duyệt. Không làm.
  - Phần A làm được (kiểm host và địa chỉ công khai, ghim DNS cho mỗi tunnel) không bù được hai điểm trên. Theo yêu cầu, không nới AGENTS.md cho A.
- B đạt, với một thay đổi so với đề xuất M0: **không tự làm cookie jar**. Spike và test trên Edge thật cho thấy:
  - yêu cầu bị chặn đã mang sẵn header Cookie do Edge tính (domain, path, secure, hạn, SameSite; `credentials: 'omit'` thì không có);
  - `Set-Cookie` trong phản hồi do Python trả về được chính Edge áp dụng theo luật chuẩn (cookie có `Domain` của host khác bị Edge bỏ).
  - Vì vậy nhược điểm "BiliFlow phải tự làm lại luật cookie" của B ở 9.4 không còn.

**Thiết kế đã làm** (`download_account_browser.py`, `download_account_http.py`):
1. Mọi yêu cầu của trang, khung và popup đi vào một route handler. Handler từ chối (`route.abort`) hoặc trả lời bằng đúng một lượt trao đổi đã kiểm của `SessionHttp` (`route.fulfill`). Không dùng `route.continue_` hay `route.fallback`. Lỗi bên trong handler cũng từ chối yêu cầu (`HANDLER_ERROR`), kể cả khi việc ghi nhận lỗi cũng hỏng. Yêu cầu đầu tiên của popup đến trước khi popup có frame; handler vẫn xử lý được, kể cả khi yêu cầu đó bị redirect.
   - Yêu cầu multipart có phần file bị từ chối (`UPLOAD_REFUSED`). Playwright bỏ byte của file khỏi thân đưa cho handler: đo được form có một file 1.300 byte chỉ còn thân 284 byte, phần file chỉ còn header. Gửi đi thì server nhận một yêu cầu bị cắt, kèm cookie. Thân là một Blob thường thì vẫn đi nguyên, và ô file để trống (`filename=""`, không có byte nào để mất) vẫn được gửi (test).
2. `SessionHttp`, cho từng yêu cầu:
   - chỉ https, cổng mặc định, host nằm trong danh sách của đúng nguồn (so khớp chính xác). Host khác bị từ chối trước khi tra DNS;
   - `SafeHttp.check`: mọi địa chỉ phải công khai; socket chỉ nối tới địa chỉ vừa kiểm (DNS ghim tại kết nối thực tế, không tra lần hai);
   - TLS kiểm chứng chỉ theo tên host (SNI). Luồng TLS bị cắt không có close_notify là lỗi, không phải hết thân;
   - hạn giờ cho cả lượt, mặc định 30 giây: DNS chạy trong luồng phụ và bị giới hạn, mỗi địa chỉ chỉ được phần thời gian còn lại, watchdog đóng socket khi hết giờ;
   - hủy: socket được giữ ngay khi có (TCP, rồi TLS trước khi bắt tay), nên hủy lúc đang nối hay đang bắt tay dừng trước khi gửi byte nào. Hủy lúc đang tra DNS cũng dừng ngay: luồng DNS được chờ từng lát 50 ms và xem `control` giữa các lát;
   - giới hạn byte của thân, kể cả sau giải nén. Mặc định 16 MiB; thân ngắn hơn Content-Length là lỗi; nén lạ hay hỏng là lỗi;
   - thân gửi đi tối đa 1 MiB; chỉ GET, HEAD, POST, OPTIONS; phản hồi 1xx (như 103) bị từ chối;
   - bỏ header hop-by-hop, điều kiện và Range; không tự thêm cookie. Giá trị header UTF-8 (Location có dấu) được giữ đúng;
   - tùy chọn có trần (thời gian tối đa 120 giây, thân tối đa 64 MiB). Mặc định production (resolver hệ thống, `is_public_address`, TLS `CERT_REQUIRED` + kiểm tên host) được test chốt.
3. Một phản hồi 3xx không bao giờ được trả cho Edge dưới dạng redirect, vì Edge tự đi theo redirect được fulfill, ngoài lớp chặn (test `test_a_fulfilled_redirect_would_escape_the_interception_so_none_is_used`: Edge tự gửi yêu cầu tới đích, và yêu cầu đó dừng ở hố đen).
   - Điều hướng bị redirect nhận một "trang chuyển tiếp" nhỏ: `location.replace(<đích đã kiểm>)`, không gửi Referer, mang `Set-Cookie` của chính URL redirect. Bước kế tiếp là một yêu cầu mới, được kiểm lại như mọi yêu cầu.
   - Đích chỉ khác URL redirect ở fragment: `location.replace` khi đó chỉ cuộn trang, không gửi yêu cầu. Trang chuyển tiếp đặt URL đích (`history.replaceState`) rồi tải lại. Edge tự quyết định có phải trường hợp này không, trên URL nó đã phân tích, nên cách viết URL khác nhau hay `#` rỗng không làm sai. Test: POST → 303 về chính URL có fragment thì lần tải lại là GET không thân, form không bị gửi lại; vòng lặp về chính URL có fragment dừng ở `MAX_HOPS`.
   - `navigate` tới chính tài liệu đang hiện, chỉ thêm hay đổi fragment, thì chỉ cuộn trang: trả về ngay với status của tài liệu đó, không chờ một tài liệu không bao giờ tới.
   - Tối đa `MAX_HOPS` = 10 trang chuyển tiếp liên tiếp trong một khung (`TOO_MANY_REDIRECTS`).
   - `navigate(url)` chờ tới tài liệu của câu trả lời cuối (sau các trang chuyển tiếp). Tài liệu chỉ được tính khi khung đã mang đúng URL của câu trả lời đó (bỏ fragment). URL và status được chốt lúc đó, nên trang tự đi tiếp sau đó không đổi kết quả (test).
   - Cả lượt `navigate` có chung hạn `page_seconds`: các bước chuyển tiếp, yêu cầu của trang và lúc DOM sẵn sàng. Các handler chạy tuần tự, nên vòng chờ chỉ thấy giờ giữa hai handler; vì vậy handler tự từ chối mọi yêu cầu đến sau hạn (`TIMEOUT`). DOM chưa sẵn sàng trong hạn thì báo `NAVIGATION_TIMEOUT`. Test: sáu script, mỗi cái 1,5 giây, hạn 2 giây; lượt dừng sau khoảng 3 giây thay vì 9 giây.
   - Lỗi chỉ nêu host (`BrowserFailed`); hủy thì `Cancelled`. `launch_seconds` và `page_seconds` phải lớn hơn 0 và không quá 600.
   - Bị từ chối: redirect của yêu cầu không phải điều hướng (fetch, ảnh, script); đích ngoài nguồn, http, `javascript:`, `data:`, có userinfo; 307/308 sẽ gửi lại thân (form đăng nhập) sang URL khác. `Set-Cookie` của redirect bị từ chối không được áp dụng.
4. Đường ngoài lớp chặn. Không phải mọi thứ đều tới handler (probe và test trên Edge thật): fetch của worker thường có đi qua handler, nhưng WebSocket của worker không được route; mọi yêu cầu của SharedWorker không có frame nên Playwright tự cho đi tiếp, không qua handler; Edge còn có lưu lượng nền riêng (tới các máy chủ dịch vụ của Microsoft: cập nhật, SmartScreen, tìm kiếm). Chốt chặn thật cho những đường đó:
   - proxy duy nhất của Edge, kể cả loopback, là một "hố đen" trong tiến trình trên 127.0.0.1: đóng mọi kết nối, không chuyển tiếp gì;
   - `--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE 127.0.0.1` để Edge không tự phân giải tên nào;
   - tắt QUIC, background networking, ping, DNS prefetch;
   - **WebRTC:** Edge không đọc cờ `--force-webrtc-ip-handling-policy`. Test giữ nguyên API WebRTC, trỏ STUN/TURN vào bộ nghe UDP cục bộ: chỉ có cờ thì bộ nghe nhận 8 gói UDP. Vì vậy profile mới được ghi sẵn `Default/Preferences` với `webrtc.ip_handling_policy = disable_non_proxied_udp` trước khi Edge chạy; khi đó 0 gói. Test cặp (`test_without_the_profile_preference_webrtc_would_send_udp` và `test_webrtc_with_its_api_kept_still_sends_nothing`) giữ bằng chứng này;
   - Edge chạy **có sandbox** (`chromium_sandbox=True`; mặc định Playwright thêm `--no-sandbox`), kiểm trên dòng lệnh thật của các tiến trình Edge. Edge chỉ nhận các biến môi trường cơ bản của Windows, `TEMP`/`TMP` trỏ vào profile.
   - Lớp phòng thủ thêm: gỡ API WebRTC trong mọi khung và popup; WebSocket của trang được route tới một mock không bao giờ `connect_to_server` (gọi `WebSocketRoute.close()` trong handler làm Playwright 1.63 treo `goto`); service worker bị chặn; tải xuống bị từ chối; yêu cầu media, EventSource và ping bị hủy; bỏ header `Alt-Svc`, `NEL`, `Report-To`, `Reporting-Endpoints`.
5. Cookie và storage nằm trong jar của Edge. Phiên được nạp bằng `set_storage_state(own_state(...))`; Playwright nạp localStorage qua interceptor riêng, không ra mạng. Phiên được đọc lại cũng qua `own_state`. Mỗi context chỉ chứa một nguồn, nên cookie của nguồn khác không có trong đó.
6. Profile tạm của mỗi lượt: `SessionVault.new_browser_profile()` → `temp\source-account-browser\<SID>\run-<16 hex>`.
   - Thư mục SID có ACL riêng, kiểm như kho phiên; profile được tạo bằng `mkdir` thường ngay sau khi kiểm lại thư mục, để thừa kế ACL đó (`tempfile.mkdtemp` 0o700 đặt ACL bảo vệ riêng trên Windows).
   - `TEMP`, `TMP`, tải xuống và artifact của Playwright đều nằm trong profile. `launch()` thường tạo profile ở ổ C, nên dùng `launch_persistent_context`.
   - Trong lúc Edge chạy, file `.biliflow-run` được mở sẵn trong profile (`pin_browser_profile`). Handle mở này giữ profile và mọi thư mục phía trên khỏi bị đổi tên hay thay bằng junction: probe và test thật đều nhận `PermissionError` (winerror 5).
   - Ghim chỉ nhận đúng `run-<16 hex>` nằm trực tiếp trong thư mục tài khoản. Sau khi giữ handle, nó kiểm lại thư mục; thư mục đã bị thay thì bị từ chối và handle được đóng.
   - Khi đóng, `remove_browser_profile` chỉ xóa thư mục `run-<16 hex>` nằm trực tiếp trong thư mục của tài khoản; link hay junction bị gỡ, không đi theo; thử lại 3 lần; kết quả xóa được kiểm lại. Không xóa được thì `profile_left = True`.
   - `AccountManager.clear_browser_profiles()` xóa các `run-*` do lượt bị giết để lại; M4 gọi lúc khởi động.
7. Không log. `refusals` chỉ giữ trong bộ nhớ mã lỗi, host và loại tài nguyên; hố đen chỉ giữ tên host. Lời lỗi chỉ nêu host. Lỗi của Playwright (chứa nguyên URL, có thể có vé) không bao giờ được chuyển tiếp hay giữ làm context của lỗi mới (`BrowserFailed`, `SESSION_LOAD_FAILED`, `SESSION_READ_FAILED`, và `BrowserUnavailable` khi Edge không khởi chạy được: lỗi này chỉ nêu tên lớp lỗi). Trình duyệt không khởi chạy khi `DEBUG` nhắc tới Playwright (`pw…`, `*`) hay khi có `PWDEBUG`, vì driver của Playwright sẽ in giao thức (URL, cookie, thân) ra stderr.
8. Tối đa `MAX_PAGES` = 8 trang cùng lúc; popup vượt mức bị đóng.

**Gắn kết quả với manager, root, nguồn và generation (mục 5 của yêu cầu M2a):**
- `LoginAttempt` và `SessionLease` mang `owner` = (root đã resolve, SID của tiến trình lấy từ kho phiên, không từ cấu hình hay request).
- `complete_login`, `end_login`, `attempt_is_current`, `save_rotated`, `mark_invalid(lease)` và `record_check(lease, ...)` từ chối, trước khi đọc hay ghi gì, một lượt hay lease của manager khác, kể cả khi trùng `source_id` và generation.
- `mark_invalid` và `record_check` đổi từ `(source_id, generation)` sang lease: kết quả trình duyệt nào cũng phải đi kèm lease đã sinh ra nó.
- `run_with_session(manager, source_id, action)` lấy lease, chạy context headless và chỉ trả cookie xoay vòng qua `manager.save_rotated(lease, ...)`.
  - Sau ngắt kết nối hay sau lượt đăng nhập mới, lease cũ bị từ chối (`saved=False`) và phiên mới giữ nguyên.
  - Lỗi Playwright trong `action` thành `BrowserFailed("BROWSER_FAILED")`, không lưu gì; lỗi khác của `action` đi ra nguyên vẹn. Profile vẫn bị xóa.
  - Lỗi SQLite lúc lưu thì `saved=False`, kết quả của `action` vẫn trả về.
  - Lease có cookie mà trạng thái cuối không còn cookie nào (đăng xuất) thì không lưu (`saved=False`); phiên đã lưu giữ nguyên. M3 sẽ báo việc này cho manager.
  - "Có thay đổi" được tính trên toàn bộ trạng thái (`_comparable`, sửa P2 của review Codex):
    - mọi key của mọi cookie đúng như Playwright trả về (value, expiry không làm tròn, `httpOnly`, `secure`, `sameSite`, `partitionKey` nếu có, và key khác nếu có);
    - key, cookie, origin và mục localStorage được sắp thứ tự, nên khác thứ tự không thành thay đổi.
    - Server giữ nguyên giá trị cookie mà chỉ siết thuộc tính (thêm HttpOnly, SameSite=Strict) thì thuộc tính mới được lưu, giữ nguyên generation, `authenticated_at` và mốc TTL; lượt ẩn sau nạp đúng thuộc tính đó.
    - Edge trả lại expiry chính xác qua mỗi lần nạp và đọc lại (test), nên phiên không đổi không bị lưu lại.
  - `SessionRun.profile_left` báo profile chưa xóa được.

**File đổi:**
- Mới:
  - `src/biliflow/download_account_http.py`;
  - `src/biliflow/download_account_browser.py`;
  - `tests/test_download_account_http.py`;
  - `tests/test_download_account_browser.py`.
- Sửa:
  - `src/biliflow/download_accounts.py` (owner của lượt và lease; `mark_invalid`/`record_check` theo lease; `clear_browser_profiles`);
  - `src/biliflow/download_account_vault.py` (`browser_folder`, `new_browser_profile`, `pin_browser_profile`, `remove_browser_profile(s)`);
  - `tests/test_download_account_vault.py` (thư mục, profile và ghim của trình duyệt);
  - `tests/test_download_accounts.py` và `tests/test_download_account_scope.py` (chữ ký mới);
  - `AGENTS.md`, `docs/VIDEO_DOWNLOAD_PLAN.md` và tài liệu này.
- Không sửa `download_http.py`, `SafeHttp`, provider ẩn danh hay file nào trong khóa cache quét.

**Bằng chứng (test thật, mock và skip tách riêng):**
Chạy trong worktree bằng `.venv` của bản chính (Python 3.11, Playwright 1.63 có sẵn) và Edge 154 đã cài trên máy (`channel="msedge"`, headless). Không cài hay tải gì. `PYTHONPATH` trỏ vào worktree, `TEMP`/`TMP` = `temp\m2a-tmp`, mọi root tạm dưới `E:\DungChung\BiliFlow\temp`. Lượt cuối 2026-10-08:
- `tests.test_download_account_http`: 34 test, OK, không skip.
  - Thật: socket TCP và TLS thật tới server fixture HTTPS trên 127.0.0.1, chứng chỉ của một CA thử tạo lúc chạy; byte trên dây, header, thân và địa chỉ được ghi lại ở phía server.
  - Tiêm qua `SessionNetwork` (đường duy nhất tới địa chỉ cục bộ): resolver giả trả địa chỉ công khai cho tên `.example`, connector đưa socket tới fixture.
  - Kiểm: host ngoài danh sách bị chặn trước DNS; địa chỉ nội bộ và DNS rebinding; CA lạ, sai tên chứng chỉ; hạn giờ của cả lượt (DNS treo, header chậm, thân nhỏ giọt, bắt tay TLS im lặng); hủy lúc đang tra DNS, lúc đang nối, lúc bắt tay, lúc đọc thân; thân quá lớn (có và không có độ dài, chunked), bom nén, thân bị cắt, EOF không có close_notify, nén lạ hay hỏng; 103; HEAD; Location UTF-8; mặc định production và trần tùy chọn; `SafeHttp` vẫn từ chối mọi header Cookie.
- `tests.test_download_account_browser`: 59 test, OK, không skip.
  - 47 test chạy trên **Edge headless thật**. Mạng cũng thật nhưng chỉ trên loopback: fixture server qua `SessionHttp`, bộ nghe TCP và UDP trên 127.0.0.1, hố đen. Ba test trong số đó tiêm lỗi bằng mock để kiểm đường lỗi (`SessionHttp.send` ném lỗi, `save_rotated` ném lỗi SQLite, `remove_browser_profile` trả False); Edge vẫn chạy thật.
  - 12 test không cần trình duyệt: so sánh trạng thái phiên (mọi thuộc tính, không theo thứ tự), trang chuyển tiếp (cả kiểu tải lại), lọc header, môi trường của Edge, cờ khởi chạy, tùy chọn WebRTC ghi vào profile, giới hạn `launch_seconds`/`page_seconds`, hố đen (socket thật), handler lỗi hai lần (route giả). Hai test dùng mock thay cho Playwright: `DEBUG`/`PWDEBUG` chặn khởi chạy trước khi tạo gì; lỗi khởi chạy giả có vé mồi không để lại chữ nào của lỗi Playwright và không để lại profile.
  - Đăng nhập và phiên: form POST giả có mật khẩu mồi; cookie đặt ở bước redirect đi qua trang chuyển tiếp; tái sử dụng ở context thứ hai (cookie theo host, path, hạn; localStorage); cookie và storage của nguồn khác không vào context; `Domain` lạ bị Edge bỏ; `credentials: 'omit'` không gửi cookie; cookie xoay vòng được lưu cùng lease, không đổi `authenticated_at`. Mật khẩu và cookie mồi không có trong `refusals`, hố đen, trạng thái công khai, DB (kể cả `-wal`) hay phiên đã lưu.
  - Redirect: trong nguồn (yêu cầu mới, không Referer, đếm DNS); ra ngoài nguồn, http, `javascript:`, `data:`, userinfo, `//host`, tương đối; 307 có thân; vòng lặp (`MAX_HOPS` + 1 yêu cầu rồi dừng); `navigate` trả URL và status cuối, kể cả khi trang chuyển tiếp trùng URL đích, và giữ đúng tài liệu đã chờ khi trang tự đi tiếp; đích chỉ khác fragment, cả sau POST (lần tải lại là GET không thân), và vòng lặp kiểu đó; `navigate` chỉ đổi fragment trả về ngay, server không nhận yêu cầu mới; Location có dấu (UTF-8) tới đúng đường dẫn mã hóa; popup có yêu cầu đầu bị redirect; lỗi chỉ nêu host khi URL có vé mồi; redirect được fulfill thì Edge tự đi tiếp tới hố đen (cả host của nguồn lẫn `http://127.0.0.1`), bộ nghe loopback nhận 0 kết nối.
  - Chặn: host ngoài danh sách không được tra DNS; địa chỉ nội bộ; DNS rebinding; CA lạ và sai tên; quá lớn, quá chậm (trang vẫn chạy tiếp); hạn chung của `navigate` (sáu script chậm, server chỉ nhận một phần); lỗi trong handler; upload qua form và qua `FormData` bị từ chối, server không nhận gì, còn form multipart không có file và thân Blob thường thì tới server nguyên vẹn.
  - Đường ngoài lớp chặn, mỗi test kiểm bộ nghe TCP và UDP trên 127.0.0.1 (0 kết nối, 0 gói), DNS và host phía server: service worker; WebSocket của trang và của worker; worker thường và SharedWorker (fetch, WebSocket; hố đen nhận cả `127.0.0.1` lẫn host ngoài nguồn từ worker); WebRTC bị gỡ API trong khung, popup; WebRTC giữ API với STUN/TURN trỏ vào bộ nghe: 0 gói khi có tùy chọn profile, có gói khi bỏ nó (test đối chứng); speculation rules; `<a ping>`; EventSource; header `Link` preconnect; header `Refresh`; beacon; trang đọc được `null` cho `Alt-Svc`, `NEL`, `Report-To`, `Reporting-Endpoints`; popup được kiểm như trang; tối đa `MAX_PAGES` trang.
  - Khởi chạy: dòng lệnh thật của các tiến trình Edge (qua `Win32_Process`): không tiến trình nào có `--no-sandbox`; tiến trình chính có `--headless`, đủ cờ và đúng proxy; không còn tiến trình nào sau khi đóng.
  - Gắn kết quả: hủy đăng nhập khi trình duyệt đang chạy; ngắt kết nối và đăng nhập mới giữa một lượt ẩn; hai manager cùng nguồn và cùng generation (khác root, khác SID) không nhận kết quả của nhau; action lỗi không mang URL và không lưu gì; lỗi SQLite lúc lưu; phiên mất hết cookie (đăng xuất) không được lưu; cookie giữ giá trị nhưng đổi thuộc tính được lưu (cùng generation, `authenticated_at`, TTL) và context sau nạp đúng thuộc tính mới; thay đổi thuộc tính của lease cũ hay của manager khác bị từ chối; phiên không đổi (cookie có expiry lẻ giây) không bị lưu lại; profile sót được báo rồi được dọn; ACL thật của profile, và trong lúc Edge chạy không đổi tên được profile hay thư mục chứa nó; hủy chặn mọi yêu cầu sau đó.
- `tests.test_download_account_vault`: 29 test, OK. Có 10 test cho thư mục trình duyệt: ACL thật; junction ở mọi cấp; profile mới rỗng và riêng; chỉ xóa `run-<16 hex>`; junction thay profile và junction bên trong profile chỉ bị gỡ link, đích còn nguyên; profile không xóa được được đếm và để lần sau; ghim giữ profile và mọi thư mục phía trên không đổi tên được; ghim từ chối thư mục không phải profile mới và không để lại gì; ghim từ chối khi thư mục tài khoản đã bị thay.
- Regression M1: `tests.test_download_accounts` 44, `tests.test_download_account_scope` 12, `tests.test_download_account_config` 11, đều OK.
- Toàn bộ `test_download*.py` (có `BILIFLOW_FFMPEG` trỏ tới FFmpeg của bản chính): 642 test, OK, không skip, 318 giây.
- `tests.test_cache_dependencies` và `tests.test_stage_cache`: 17 test, OK. Không đụng file nào trong khóa cache quét.
- Đỏ trước, xanh sau (ghi lại trong lúc làm):
  - test WebRTC giữ API: 8 gói UDP trước khi ghi tùy chọn profile, 0 sau đó;
  - popup và `MAX_PAGES`: thất bại trước khi `_frame_state` xử lý yêu cầu đầu của popup chưa có frame (lúc đó yêu cầu bị treo, không abort);
  - test redirect được fulfill tới 127.0.0.1: lần đầu không thấy 127.0.0.1 ở hố đen vì chính test truyền sai `Location` (handler hai tham số của Playwright); đã sửa test, Edge đi đúng tới hố đen;
  - test hạn chung của `navigate`: lần chạy đầu báo `BROWSER_FAILED` vì `wait_for_load_state` có hạn riêng tính từ lúc nó bắt đầu; đã sửa để dùng thời gian còn lại và báo `NAVIGATION_TIMEOUT`;
  - probe upload (ngoài repo): thân nhận được 284 byte cho form có file 1.300 byte, trước khi có `UPLOAD_REFUSED`.
- Không test nào chỉ dựa trên mock mà được tính là bằng chứng trình duyệt chạy.

**Spike (ngoài repo, `temp\m2a-spike`, Edge 154 headless + Playwright 1.63), kết quả dùng để chọn thiết kế:**
- Cookie: `Set-Cookie` trong `route.fulfill` được lưu, kể cả HttpOnly và Secure; cookie `Domain` lạ bị Edge từ chối; yêu cầu bị chặn mang header Cookie của Edge; `storage_state` dùng lại ở context thứ hai gửi đúng cookie.
- Redirect được fulfill: Edge tự đi tiếp, ngoài lớp chặn. Không có proxy thì Edge tự tra DNS (`ERR_NAME_NOT_RESOLVED`); có hố đen thì Edge gửi `CONNECT portal.example:443` tới hố đen.
- Không có lớp chặn mạng: WebSocket tới 127.0.0.1 nối thẳng TCP, và WebRTC gửi 4 gói STUN UDP.
- `chromium_sandbox=True` chạy được: 0/7 tiến trình Edge có `--no-sandbox` (mặc định: 6/7).
- Edge gửi lưu lượng nền (tới các máy chủ dịch vụ của Microsoft) và kết nối mở trước tới host của trang; tất cả tới hố đen và bị đóng.
- Sửa kết luận của spike bằng test sau đó: spike thấy "0 gói WebRTC khi đã khóa" vì API bị gỡ cùng lúc; tách riêng thì cờ dòng lệnh không có tác dụng (điểm 4). Spike cũng ghi "yêu cầu của popup và dedicated worker đi qua route": đúng với fetch của worker thường, nhưng WebSocket của worker và mọi yêu cầu của SharedWorker không qua route (điểm 4).

**Giới hạn còn lại:**
- Redirect của fetch, XHR, ảnh và script bị từ chối (chưa đi theo từng bước trong Python, vì mỗi bước cần cookie của đúng URL). 307/308 có thân bị từ chối kể cả khi cùng origin. Có thể làm hỏng một số luồng đăng nhập thật; M2b/M7 xem lại bằng luồng thật.
- Handler của Playwright sync chạy tuần tự: mỗi yêu cầu chờ yêu cầu trước. Một tài nguyên chậm giữ trang tối đa bằng hạn giờ của nó (30 giây). Trong `navigate`, yêu cầu đến sau hạn của lượt bị từ chối, nên lượt vượt hạn nhiều nhất một yêu cầu. Ngoài `navigate` (adapter tự click hay chờ) thì chưa có hạn chung như vậy.
- Upload file bị từ chối hoàn toàn. Luồng nào cần gửi file sẽ không chạy.
- `framenavigated` của Playwright đến cả với điều hướng trong cùng tài liệu (`pushState`, đổi hash). Nếu tài liệu cũ ở cùng URL phát một sự kiện như vậy đúng trong khoảng giữa câu trả lời mới và lúc tài liệu mới vào khung, `navigate` có thể trả về sớm với DOM cũ. Cần `navigate` tới đúng URL đang hiện và một script của trang chạy trong khoảng vài mili giây đó (review lần ba, LOW; chưa sửa).
- Yêu cầu đã xếp hàng trước khi `navigate` trả về nhưng được xử lý sau đó thì không còn hạn chung (chỉ còn hạn của từng yêu cầu). `_frames` giữ tham chiếu khung tới hết lượt. `open()` gọi trực tiếp, không qua `with`, mà lỗi thì không tự dọn proxy, ghim và profile; chỉ dùng qua `with` như docstring.
- Header gửi đi theo danh sách loại trừ, không theo danh sách cho phép (review M2a, M6): `Origin`, `Referer`, `Sec-*`, `Authorization` và header CSRF đi qua, vì luồng đăng nhập cần chúng. Header Cookie là của chính Edge; chỉ tới host của đúng nguồn.
- Header điều kiện và Range bị bỏ, nên Edge không dùng cache 304. Media vẫn bị chặn như thiết kế.
- Chỉ GET, HEAD, POST, OPTIONS. Trang cần PUT, PATCH hay DELETE sẽ không chạy.
- Trang chuyển tiếp để lại URL redirect trong lịch sử và làm `document.referrer` rỗng ở trang đích.
- Trang cần host ngoài cấu hình (nhà cung cấp danh tính, CDN, khung thử thách) sẽ không chạy cho tới khi host đó được thêm vào cấu hình của nguồn. Danh sách host thật chỉ biết được khi xem cùng người dùng (M7).
- WebSocket của trang chỉ là mock không bao giờ nhận dữ liệu; yêu cầu của SharedWorker và WebSocket của worker dừng ở hố đen. Trang cần các đường đó sẽ không chạy đúng.
- QUIC không đo trực tiếp được. Bằng chứng gồm:
  - cờ `--disable-quic`;
  - `Alt-Svc` bị bỏ (test thấy trang đọc được `null`);
  - mọi phản hồi được trả trong tiến trình;
  - bộ nghe UDP nhận 0 gói trong mọi test.
- Trong lúc context chạy, cookie nằm trong profile của Edge (Edge mã hóa cookie bằng khóa DPAPI của người dùng; localStorage không mã hóa). Profile nằm trong thư mục ACL riêng và bị xóa khi đóng. Tiến trình bị giết giữa chừng để lại `run-*` (vẫn ACL riêng) cho tới khi M4 gọi `clear_browser_profiles()` lúc khởi động.
- Chỉ đã kiểm headless. M2b phải kiểm lại cùng lớp khóa này ở chế độ headed (giao diện Edge có thể có thêm lưu lượng), và tắt trình quản lý mật khẩu, tự điền của Edge trong profile (review M2a, L6). M2b (mục 9.11): đã tắt trong profile và kiểm Edge giữ các tùy chọn đó (headless); tùy chọn khởi chạy của cửa sổ chỉ khác `headless` (kiểm bằng runtime giả). Lưu lượng và giao diện của cửa sổ thật chưa kiểm.
- `route.request.headers` của Playwright 1.63 có header Cookie (test kiểm). Nếu bản Playwright sau bỏ header này thì phiên không gửi được cookie (hỏng an toàn), không lộ cookie.
- Hai lượt ẩn dùng cùng một lease thì lượt lưu sau thắng (review M2a, L3). M3/M4 phải cho mỗi nguồn chạy một lượt ẩn tại một thời điểm.
- `Target` của `download_http` có URL trong `repr` (review M2a, L5). `SessionHttp` không lưu hay log nó; `download_http.py` không sửa ở nhánh này.
- `check_link` mã hóa lại `|`, `{`, `}` trong đường dẫn. Vô hại, trừ link ký số có chữ ký tính trên đường dẫn thô; M3 xem lại với link thật.
- `run_with_session` có tham số `network`/`http_options` để test tiêm fixture. Code production (M3/M4) không được truyền chúng; tùy chọn có trần và mặc định đã được test chốt (review M2a, L1).
- `context` và `page()` là đối tượng Playwright thật. Quy tắc cho adapter (không thêm route, không `continue_`, không `context.request`/`page.request`) mới nằm trong docstring; M3 cần test hay review để giữ.
- Đường lỗi `SESSION_LOAD_FAILED` chưa có test (trạng thái đã qua `check_storage_state` không làm `set_storage_state` lỗi).
- Provider `embedded-media` hiện có ở `main` chạy Edge với `--no-sandbox` (mặc định của Playwright), fulfill 302 cho tài liệu bị redirect (đích được Edge tự tải ngoài `SafeHttp`) và gọi `socket.close()` trong handler WebSocket, chỗ spike thấy treo. Không sửa ở nhánh này; đã đề xuất một việc riêng.
- Chưa thử bằng site thật hay hai tài khoản Windows thật.

### 9.10 Yêu cầu bổ sung (2026-10-08): dán trang phim và chọn tập

Nguồn: tài liệu yêu cầu riêng của người dùng, ngoài repo. Link ví dụ trong đó chỉ là ví dụ riêng: tên miền, URL và tên phim không vào code, test hay tài liệu của repo. Danh sách tập của trang ví dụ chưa được xác nhận; không suy ra phim lẻ hay số tập từ đường dẫn. Mục này là thiết kế đề xuất, chưa có code. M2b–M7 làm từng phần, mỗi phần qua review trước khi sang phần sau.

**Hành vi bắt buộc (tóm tắt):**
1. Người dùng vẫn dán link trang phim vào ô tải hiện có. Nguồn được nhận diện bằng host chính xác trong cấu hình local. Adapter đọc trang; không đòi người dùng lấy link media hay link vé, không đòi đường dẫn có tên phim cố định.
2. Thiếu phiên: chờ đăng nhập đúng nguồn, không chiếm slot. Cửa sổ đăng nhập chỉ mở khi người dùng bấm. Danh sách tập và file được đọc ẩn khi có phiên.
3. Một file phim hợp lệ: luồng phim lẻ hiện có; nhiều bản chất lượng hay âm thanh thì người dùng chọn bản trước.
4. Phim nhiều tập:
   - hiện tên phim, mùa nếu có, tên hay số tập, các bản tải;
   - hai chế độ: "Tải tất cả các tập đang có", hoặc "Chọn tập" (checkbox từng tập, chọn tất cả, bỏ chọn, chọn được nhiều tập);
   - hiện số tập đã chọn và nút "Tải N tập". Chỉ tạo tác vụ sau khi người dùng bấm nút này.
5. "Tải tất cả" phải đọc đủ danh sách hiện có (mùa, phân trang mà adapter hỗ trợ) và cho thấy phạm vi trước khi bấm.
   - Chưa đọc đủ hay bị giới hạn: báo rõ danh sách chưa đầy đủ, không gọi một trang kết quả là tất cả.
   - Không tự tải tập đăng thêm sau này.
6. Mỗi tập chỉ tải một bản người dùng chọn. Bản (chất lượng, âm thanh) là lựa chọn file của tập, không phải tập mới.
   - Tập không có bản đó: báo rõ.
   - Không tải mọi bản, không tự đổi bản.
7. Mỗi tập đã chọn là một tác vụ con, gắn với trang phim ban đầu bằng id nhóm.
   - Thứ tự: mùa, rồi số tập theo số (2 trước 10); tập đặc biệt có vị trí rõ ràng.
   - Không xác định được số: giữ thứ tự trang và tên gốc, không đoán.
8. Tên file có số thứ tự độ rộng cố định trong nhóm, ví dụ `001 - <Tên phim> - S01E01.mkv`, để sắp theo tên vẫn đúng thứ tự dù tập xong theo thứ tự khác.
   - Làm sạch theo quy tắc hiện có, không ghi đè file đã có.
   - Giữ MKV, chưa chuyển MP4.
9. Tiến độ:
   - mỗi tập có tiến độ riêng; nhóm hiện "đã xong N/tổng", tập đang chạy và tập lỗi;
   - phần trăm cả nhóm chỉ khi đủ kích thước đáng tin, không tạo phần trăm giả.
10. Vé chỉ xin ngay trước khi tập dùng slot tải: không xin khi mở danh sách hay khi bấm Tải tất cả.
    - Thao tác trình duyệt (đọc, lấy vé) của một nguồn chạy tuần tự để hai lease không ghi đè cookie.
    - Tải file dùng giới hạn song song hiện có.
    - Không mở cửa sổ đăng nhập cho từng tập.
11. Lỗi, khôi phục và điều khiển:
    - lỗi một tập không làm mất các tập đã xong; thử lại không nhân đôi tác vụ;
    - refresh hay khởi động lại giữ lựa chọn, nhóm và thứ tự;
    - hủy nhóm áp dụng cho tập chưa xong, tôn trọng tập đã hủy hay dừng;
    - không thêm pause toàn cục.
12. API và giao diện chỉ nhận id nguồn, danh tính ổn định của phim, tập, file, bản và lựa chọn.
    - Vé, URL ký số, cookie và `storage_state` không có trong preview, trạng thái, log hay Git.
    - Nhóm dài không bị cắt âm thầm theo `MAX_BATCH_LINKS`.

**Ranh giới code hiện tại liên quan (đọc ở base `e8aea11`):**

| Chỗ | Hiện trạng | Hệ quả |
|---|---|---|
| `download_store.add_tasks` | Chặn trùng theo URL nguyên văn của các dòng chưa đóng. | Các tập chung URL trang phim, nên tác vụ của nguồn tài khoản cần khóa trùng theo danh tính tập + bản. |
| `download_store.MAX_UNFINISHED_TASKS = 100` | Lô vượt mức thì bị từ chối cả lô. | Nhóm dài có thể vượt; cần cách xử lý rõ (điểm 5 bên dưới). |
| `download_links.MAX_BATCH_LINKS = 20` | Số dòng link mỗi lần dán. | Một trang phim là một dòng; không dùng mức này để cắt nhóm. |
| NEEDS_CHOICE, `entries`, `POST /api/downloads/<id>/choose {entry_index}` | Chọn đúng một mục; `entries` chỉ trả khi NEEDS_CHOICE (`download_api.py:94`). | Dùng cho chọn bản của phim lẻ. Chọn nhiều tập cần một hành động mới. |
| `next_queued`: `ORDER BY queued_at, id`; `retry`/`resume` đặt lại `queued_at` | Thử lại đưa tác vụ về cuối hàng. | Thêm các tập theo thứ tự trong một transaction thì hàng lấy đúng thứ tự. Thử lại một tập đổi chỗ trong hàng nhưng không đổi tên hay vị trí trong nhóm. |
| `download_files.sanitize_name` (150 ký tự kể cả đuôi), `unique_target` (thêm " (2)") | Cắt cuối tên; không ghi đè. | Khi cắt phải giữ số thứ tự và mã tập, chỉ cắt phần tên phim. |
| `download_worker`: `DEFAULT_SLOTS = 2`, `MAX_SLOTS = 3`; `SLOT_STATES` | Giới hạn tải song song. | Giữ nguyên. |
| `STOPPABLE`, `RESUMABLE`, `RETRYABLE`, `cancel` | Điều khiển từng tác vụ. | Thao tác nhóm gọi lại đúng các thao tác này cho từng tập, không có đường riêng. |

**Thiết kế đề xuất:**
1. **Đọc danh sách (M3).** Provider đọc trang bằng phiên của đúng nguồn (headless, qua `run_with_session`) và trả một danh sách:
   - loại: phim lẻ hay nhiều tập; id ổn định và tên phim;
   - mùa: số, hay nhãn như "đặc biệt";
   - tập: id ổn định, số tập nếu xác định được, nhãn gốc, vị trí trên trang;
   - bản: id ổn định, nhãn chất lượng/âm thanh, kích thước nếu trang ghi;
   - `complete` và lý do khi chưa đủ (giới hạn trang, phân trang chưa hỗ trợ, một trang lỗi);
   - không có URL media, URL vé hay token.
   - Đọc hết mùa và phân trang adapter hỗ trợ trong giới hạn rõ (số trang tối đa, số tập tối đa, hạn giờ chung); chạm giới hạn thì `complete = False`.
   - Bỏ trailer, quảng cáo và mục không phải tập theo bằng chứng của trang, không đoán theo tên.
   - Danh tính: `source_id` + id phim + id tập + id bản do nguồn cấp. Không dùng URL vé, token, hay chỉ tên và kích thước.
2. **Phim lẻ.** Luồng hiện có: một bản thì đi tiếp, nhiều bản thì NEEDS_CHOICE chọn bản.
3. **Hộp chọn tập (M4 lưu, M5 giao diện).**
   - Tác vụ trang (từ link đã dán) sang NEEDS_CHOICE kiểu "chọn tập" và nhả slot. Danh sách đã đọc được lưu trong DB (không bí mật), nên mở lại được sau refresh hay restart.
   - Theo mùa; "Tải tất cả" hay "Chọn tập"; chọn tất cả, bỏ chọn; chọn bản; nút "Tải N tập". Danh sách chưa đủ thì hiện "Danh sách chưa đầy đủ: <lý do>" và nút "Tải N tập đã thấy"; không dùng chữ "tất cả".
   - Bản: người dùng chọn một bản cho cả nhóm, ví dụ chất lượng + âm thanh. Tập không có bản đó thì hiện "thiếu bản", không chọn được, không tự đổi bản. (Code từ M4/M5, mục 9.14 và 9.17: tập đó vẫn ở trong lựa chọn để không bị bỏ âm thầm, server liệt kê nó là thiếu bản và nút "Tải N tập" bị chặn cho đến khi người dùng bỏ tập hoặc đổi bản; gate M6 giữ đúng hành vi này.)
   - Tập đã nằm trong một tác vụ chưa xong (cùng danh tính) hiện "đã có trong danh sách" và không được tạo lại.
   - Bấm "Tải N tập": một hành động mới, ví dụ `POST /api/downloads/<id>/episodes` với chế độ, khóa các tập, khóa bản và dấu vân tay của danh sách đã lưu.
     - Server kiểm mọi khóa thuộc danh sách đã lưu.
     - Trong một transaction, server tạo nhóm và các tác vụ con, rồi đóng tác vụ trang bằng một trạng thái kết thúc riêng (đề xuất `EXPANDED`, "Đã tách thành N tập"): không phải bản tải, không chặn link, không có Thử lại.
     - Gửi lại cùng yêu cầu (bấm hai lần, mạng gửi lặp) không tạo trùng, nhờ khóa theo tác vụ trang + danh tính tập.
4. **Nhóm, thứ tự và tên (M4).**
   - Bảng mới `download_batches`: nguồn, link trang (đã lọc như `public_url`), id và tên phim, chế độ, bản đã chọn, danh sách thành viên theo thứ tự (khóa, mùa, số tập, nhãn, vị trí), `complete` và lý do, thời điểm tạo.
   - Cột mới ở `download_tasks` (qua `_ADDED_COLUMNS`): `batch_id`, `batch_position`, `item_key`. Tác vụ của nguồn tài khoản chặn trùng theo `item_key` của các dòng chưa đóng.
   - Thứ tự: mùa tăng dần, rồi số tập theo số.
     - Tập đặc biệt: theo vị trí trang ghi. Trang không ghi thì đề xuất nhóm "Đặc biệt" sau các mùa thường (chốt ở M3).
     - Không có số: giữ thứ tự trang.
     - Các tập được thêm theo thứ tự trong một transaction (cùng `queued_at`, id tăng dần).
   - Tên: `<số thứ tự> - <tên phim> - S01E01`. Độ rộng cố định trong nhóm, ít nhất 3 chữ số (001); nhóm từ 1.000 tập dùng 4. Tên được làm sạch bằng `sanitize_name`; khi phải cắt cho vừa 150 ký tự thì chỉ cắt phần tên phim. `unique_target` không ghi đè. Giữ MKV.
5. **Nhóm dài (M4, cần chốt).**
   - `MAX_BATCH_LINKS` không áp dụng cho nhóm. `MAX_UNFINISHED_TASKS` (100) giữ nguyên.
   - Đề xuất: nhóm lưu đủ danh sách đã chọn.
     - Tác vụ con chỉ được tạo khi còn chỗ dưới mức 100. Phần còn lại hiện "chờ chỗ trong danh sách": không slot, không vé.
     - Phần này được tạo theo đúng thứ tự khi các tác vụ trước đóng.
     - Mỗi nhóm có mức trần rõ ràng (đề xuất 500 tập); vượt mức thì báo trước khi bấm, không cắt âm thầm.
   - Phương án khác: từ chối nhóm vượt chỗ trống và báo số tập còn thêm được.
6. **Vé, trình duyệt và slot (M3, M4).**
   - Không xin vé khi đọc danh sách hay khi bấm "Tải N tập". Mỗi tập xin vé ở PROBING (lúc đó đã giữ slot) và xin lại ở DOWNLOADING như luồng hiện có.
   - Mọi thao tác trình duyệt của một nguồn (đọc danh sách, xin vé) đi qua một khóa theo nguồn trong tiến trình, nên chạy tuần tự (giới hạn L3 ở 9.9). Không chạy dưới `_lock` của worker hay khóa của store.
   - File được tải bằng `FileTransfer` qua `SafeHttp` không cookie, theo `slots()` hiện có (mặc định 2, tối đa 3).
   - Thiếu phiên: các tập chưa xin vé của nguồn đó chờ ở WAITING_LOGIN (không slot); đăng nhập xong thì đánh thức theo thứ tự. Một cửa sổ đăng nhập cho cả nguồn, không phải cho từng tập.
7. **Tiến độ và điều khiển (M4, M5).**
   - Từng tập: tiến độ hiện có. Nhóm: "đã xong N/tổng", tập đang chạy, tập lỗi. Phần trăm cả nhóm chỉ khi mọi tập có kích thước đáng tin; không thì chỉ hiện N/tổng.
   - Lỗi một tập không ảnh hưởng tập khác. Thử lại dùng lại đúng dòng tác vụ, giữ `batch_position` và tên.
   - Hủy nhóm: hủy các tập chưa xong; tập đã xong giữ nguyên; tập đã hủy hay dừng giữ trạng thái. Dừng nhóm: Dừng từng tập đang chạy hay đang chờ. Không thêm pause toàn cục.
   - Lựa chọn đã bấm, nhóm và thứ tự nằm trong DB. Lựa chọn đang tick dở (chưa bấm) giữ trong trình duyệt theo id tác vụ trang (M5).
8. **Bí mật.** Danh sách, preview, trạng thái, sự kiện và log chỉ có id nguồn, danh tính ổn định, nhãn, số, kích thước và lựa chọn. Test dùng vé, URL ký số và cookie mồi rồi tìm chúng trong API, DB, log và thư mục tạm.
9. **Điện thoại.** Chọn tập và tải là thao tác tải như `/api/downloads*` hiện có, nên đề xuất cho phép trên điện thoại theo đúng quy tắc hiện có. Đăng nhập và ngắt kết nối vẫn chỉ trên PC.

**Fixture và test (M6, có thể làm dần từ M3):**
- phim lẻ một bản và nhiều bản;
- 12+ tập; hai mùa; tập đặc biệt; tập không có số;
- phân trang (đọc đủ, và chạm giới hạn → "chưa đầy đủ");
- nhiều bản mỗi tập, một tập thiếu bản đã chọn;
- trailer và quảng cáo xen giữa;
- chưa đăng nhập và hết phiên giữa nhóm;
- nhóm dài vượt `MAX_BATCH_LINKS` và vượt chỗ trống dưới `MAX_UNFINISHED_TASKS`;
- tập xong ngược thứ tự, tên vẫn sắp đúng;
- một tập lỗi, các tập khác vẫn xong;
- bấm hai lần, thử lại, hủy nhóm và khởi động lại không tạo trùng, không mất thứ tự;
- không lộ cookie, vé hay URL ký số.

Mọi fixture tự tạo, host `.example`.

**Nghiệm thu (M7).** Người dùng thử trang phim thật của họ trên Control Center thử, với phiên tự đăng nhập. Chỉ đọc metadata và thăm dò có giới hạn để xác nhận danh sách tập và lựa chọn; không tự tải toàn bộ phim.

**Cần người dùng hoặc Codex chốt trước khi làm phần tương ứng:**
1. Nhóm vượt chỗ trống: tạo dần (đề xuất) hay từ chối (điểm 5) — trước M4.
2. Vị trí tập đặc biệt khi trang không ghi — trước M3.
3. Có cho chọn bản riêng cho từng tập không, hay chỉ một bản cho cả nhóm (đề xuất) — trước M5.
4. Tác vụ trang sau khi tách: trạng thái `EXPANDED` (đề xuất) hay cách khác — trước M4.
5. Chọn tập trên điện thoại (đề xuất: được, như các thao tác tải khác) — trước M5.

### 9.11 Kết quả M2b (2026-10-08): cửa sổ đăng nhập chủ động và dùng lại phiên ẩn

Phạm vi: thư viện và test. Chưa nối API, giao diện hay hàng đợi (M4, M5). Không truy cập nguồn phim, không mở phiên thật, không đọc cấu hình local, DB bản chính hay biên bản riêng, không chạy Control Center, không cài gì. Mục 9.10 (dán trang phim, Tải tất cả, Chọn tập) giữ nguyên: đọc danh sách và vé ở M3, nhóm và API ở M4, giao diện ở M5.

**Đã implement:**
- `download_account_login.py`, `LoginCoordinator(manager)`: các lượt đăng nhập của một `AccountManager` (một root và một tài khoản Windows).
  - `start(source_id)` là đường duy nhất tới cửa sổ có giao diện; M4 gọi khi người dùng bấm Đăng nhập trên PC. Thứ tự kiểm:
    1. nguồn chưa cấu hình: `AccountUnknown`;
    2. adapter chưa có bộ xác nhận: `LoginUnsupported` (LOGIN_UNSUPPORTED), chưa ghi gì, chưa mở gì;
    3. nguồn đang có lượt mở: `AccountBusy` (LOGIN_BUSY), kể cả lượt của manager khác cùng root (qua DB);
    4. `begin_login` (LOGGING_IN), rồi một luồng riêng mở cửa sổ.
  - Không giữ khóa của manager hay store trong lúc cửa sổ mở. Khóa của coordinator chỉ bao bảng lượt và lời gọi `begin_login`.
  - Cửa sổ là `SessionBrowser` có `HeadedPermit(attempt)`. Nó dùng profile mới trong thư mục ACL riêng (có ghim, xóa khi đóng), cùng route handler, hố đen, cờ khởi chạy và sandbox của M2a. Không đọc profile, cookie hay mật khẩu của trình duyệt cá nhân; BiliFlow không có form mật khẩu.
  - Mở `login_url`, rồi mỗi `poll_seconds` (mặc định 1 giây) hỏi bộ xác nhận. Trước mỗi lần hỏi, kiểm theo thứ tự: đã hủy; cửa sổ đã đóng; hết hạn; lượt không còn là lượt đang mở (ngắt kết nối, lượt mới, hủy ở nơi khác: LOGIN_STALE).
  - Bộ xác nhận (`LoginVerifier.signed_in(view)`) lấy theo id adapter trong `LOGIN_VERIFIERS` của code, không từ cấu hình. Nó chỉ đọc qua `LoginView`:
    - URL của trang đang hiện;
    - chữ của một phần tử;
    - `fetch` GET từ chính trang tới host của nguồn: `in_scope` trước, rồi qua route handler với cookie của trình duyệt; tối đa 64 KiB chữ.
    - Không có `context.request`, `page.request`, route hay trang mới. Bộ xác nhận ném lỗi: VERIFIER_ERROR, không bao giờ là bằng chứng.
  - Có bằng chứng (sửa sau review M2b của Codex, P2; `_complete`), ba bước:
    1. Kiểm lại hủy, hạn và lượt, rồi thu thập: `storage_state()` (cookie, localStorage, IndexedDB; chỉ host của nguồn) vào bộ nhớ. Bước này có thể mất thời gian.
    2. Kiểm lại lần nữa sau khi thu thập: đã dừng (hủy, shutdown, hết hạn), cửa sổ đã đóng, đã quá hạn, lượt không còn là lượt đang mở. Có một điều kiện: không lưu gì, lượt kết thúc với mã đó.
    3. Commit: `complete_login` (file kho phiên rồi dòng DB, dưới khóa của nguồn): DPAPI, generation + 1, CONNECTED, `authenticated_at` = lúc lưu. **Đây là điểm commit.**
  - Phối hợp dừng và commit: hủy, shutdown và hạn chung đều đi qua `_stop_run`, theo thứ tự: báo control của lượt, rồi `end_login` đúng lượt của lượt đó (không theo nguồn) dưới cùng khóa của nguồn mà `complete_login` giữ.
    - Dừng tới trước điểm commit: `complete_login` báo StaleLogin; `_complete` đọc đó là chính lần dừng (LOGIN_CANCELLED hay LOGIN_TIMEOUT). Không lưu gì; phiên cũ giữ nguyên generation, `authenticated_at` và TTL; không có phiên mới, không gia hạn phiên cũ.
    - Dừng tới sau điểm commit: phiên mới giữ nguyên và lượt trả CONNECTED. Không có chuyện lưu rồi thu hồi hay chỉ đổi mã. `end_login` lúc đó không làm gì (lượt đã đóng); `cancel` vẫn trả True vì cửa sổ còn mở và được đóng.
    - Lượt cũ bị thay bằng lượt mới (hủy ở nơi khác rồi `begin_login`): lần dừng của lượt cũ không làm gì với lượt mới, và lượt mới vẫn commit được.
    - Manager vẫn từ chối lượt cũ hay lượt của manager khác (StaleLogin).
  - Không có bằng chứng:
    - `end_login` với LOGIN_CANCELLED, LOGIN_WINDOW_CLOSED hay LOGIN_TIMEOUT; mọi lỗi khác ghi LOGIN_FAILED (mã chi tiết nằm trong `LoginOutcome`);
    - `end_login` không làm gì khi lượt đã kết thúc hay bị thay, nên không bao giờ kết thúc một lượt mới hơn;
    - phiên cũ giữ nguyên.
  - Một hạn chung cho cả lượt (`max_seconds`, tối đa 600, tính từ ngay trước `begin_login`, nên không dài hơn hạn của manager):
    - là `stop_at` của trình duyệt: khởi chạy, trang đăng nhập và mỗi lần chờ kết thúc trước hạn, handler từ chối yêu cầu sau hạn;
    - đến hạn, một bộ hẹn giờ dừng lượt như một lần hủy (`_stop_run`, lý do `timeout`): yêu cầu đang chạy bị cắt (đóng socket) và lượt của nó kết thúc trong manager;
    - lời gọi Playwright vẫn treo (trang kẹt trong một script, Edge treo) thì `grace_seconds` (mặc định 10 giây) sau hạn hay sau khi hủy, Edge của đúng lượt bị kết thúc (`SessionBrowser.force_close`: tìm các tiến trình `msedge.exe` có `--user-data-dir` là profile riêng của lượt, rồi `kill_process_tree`). Lời gọi treo khi đó báo lỗi và luồng của lượt dọn phần còn lại như thường.
  - `cancel(source_id)`: dừng cửa sổ của coordinator và chỉ kết thúc lượt của chính cửa sổ đó (`end_login` với lượt của nó), nên không bao giờ kết thúc một lượt mới hơn. Không có cửa sổ nào ở coordinator này thì gọi `manager.cancel_login` (lượt do nơi khác để lại). Lỗi DB không làm `cancel` ném lỗi.
  - `shutdown()` (M4, khi dừng Control Center): từ chối lượt mới, dừng mọi cửa sổ như `cancel` (lượt của từng cửa sổ kết thúc trong manager, nên phiên chưa commit không được lưu), chờ tổng cộng tối đa `timeout`.
  - Kết quả `LoginOutcome(source_id, code, status, profile_left, failure)`: mã và lời cố định; `profile_left` khi profile tạm chưa xóa được; `failure` là tên lớp của lỗi bất ngờ (không có nội dung lỗi, vốn có thể chứa URL), để M4 ghi log. `repr` không có cookie, URL hay id lượt. Kết thúc lượt luôn hoàn tất `LoginRun`, kể cả khi `end_login` lỗi.
- `download_account_browser.py`:
  - `HeadedPermit`: chỉ coordinator tạo (test quét mã nguồn). `SessionBrowser(headed=…)` chỉ nhận permit của đúng nguồn: cờ `True` hay chuỗi bị TypeError, nguồn khác bị ValueError. Không có permit thì `headless=True`.
  - `run_with_session` từ chối `browser_options["headed"]` trước khi đọc phiên.
  - `stop_at` (hạn chung); `window_closed`, `pause` và sự kiện đóng context cho cửa sổ; `force_close` và `edge_processes` (chỉ tiến trình của profile riêng của lượt).
  - `storage_state(indexed_db=True)`; `set_storage_state` nạp lại cả IndexedDB.
  - `PROFILE_PREFERENCES` thêm `credentials_enable_service`, `credentials_enable_autosignin`, `profile.password_manager_enabled`, `autofill.profile_enabled`, `autofill.credit_card_enabled`, đều `false`. Edge 154 giữ các khóa này khi đóng (test). Khóa cũ `autofill.enabled` bị Edge bỏ, nên không đặt.
- `download_accounts.py`: lời nhắn của LOGIN_UNSUPPORTED.

**Test, `tests/test_download_account_login.py` (40 test):**
- Điểm commit, cửa sổ giả (4; thêm sau review M2b của Codex), mỗi trường hợp có root riêng:
  - hạn chung, shutdown hay Hủy tới trong lúc đọc trạng thái, có và không có phiên cũ: không lưu, đúng mã (LOGIN_TIMEOUT, LOGIN_CANCELLED); phiên cũ giữ generation 1, `authenticated_at` T0, `recheck_at` T0 + 3.600 giây, chỉ còn `session-1.bin`;
  - dừng tới manager ngay trước commit (sau lần kiểm cuối): commit bị từ chối, đúng mã, phiên cũ giữ nguyên;
  - dừng tới sau điểm commit (Hủy, shutdown): CONNECTED, phiên mới generation 2 với `authenticated_at` mới, không bị thu hồi;
  - lượt cũ bị thay bằng lượt mới rồi bị dừng (hạn, shutdown, Hủy): lượt mới vẫn mở và commit được, phiên cũ giữ nguyên tới lúc đó.
- Không cần trình duyệt, cửa sổ giả (18):
  - chưa nguồn thật nào có bộ xác nhận: LOGIN_UNSUPPORTED, không gọi launcher, không LOGGING_IN; nguồn lạ: AccountUnknown;
  - giới hạn `max_seconds`, `poll_seconds`, `grace_seconds`;
  - có bằng chứng ở lần hỏi thứ ba: CONNECTED, `authenticated_at`, `recheck_at` = +3.600 giây, permit gắn đúng nguồn và manager, client HTTP đúng host của nguồn; chỉ `True` là bằng chứng (`1`, `"yes"`, `[True]` thì không);
  - từng lỗi (khởi chạy, profile, trang đăng nhập, bộ xác nhận, đọc trạng thái, trạng thái sai dạng, launcher lỗi) không để lại phiên, không lộ cookie mồi; lỗi bất ngờ giữ tên lớp trong `failure`; profile chưa xóa được báo trong `profile_left`;
  - lỗi DB lúc lưu (SESSION_SAVE_FAILED); lỗi DB lúc kiểm lượt không dừng lượt; `end_login` lỗi vẫn hoàn tất lượt; không tạo được luồng: BROWSER_UNAVAILABLE và lượt được kết thúc;
  - đóng cửa sổ; hạn chung (vòng lặp hay bộ hẹn giờ thấy hạn trước, và trang đang tải chỉ bộ hẹn giờ cắt được);
  - cửa sổ treo bị kết thúc sau hạn + `grace_seconds` và sau khi hủy + `grace_seconds`; cửa sổ đóng kịp thì không bị kết thúc;
  - bấm hai lần: LOGIN_BUSY, cả từ manager thứ hai cùng root, chỉ một cửa sổ; lượt sau vẫn bắt đầu được;
  - lượt bị hủy ở nơi khác (LOGIN_STALE); lượt mới chen vào không bị cửa sổ cũ kết thúc; Hủy của người dùng chỉ kết thúc lượt của chính cửa sổ đó, không kết thúc lượt mới hơn; hủy trong lúc cửa sổ đang khởi chạy thì không mở trang đăng nhập; hủy trước khi có cửa sổ; `shutdown` rồi từ chối lượt mới.
- `LoginView` trên trang giả (3): không có trang thì không đọc gì; chỉ trang đầu, chữ có giới hạn, lỗi Playwright thành None; `fetch` tới nguồn khác, host lạ, http, cổng khác hay địa chỉ IP bị từ chối trước khi hỏi trang, câu trả lời bị cắt ở 64 KiB, dạng sai thành None, `repr` không có nội dung.
- Tùy chọn của cửa sổ, runtime Playwright giả (4):
  - chỉ permit của đúng nguồn mới bật giao diện; lượt ẩn không xin được cửa sổ (từ chối trước khi đọc phiên); chỉ `download_account_login.py` tạo `HeadedPermit`;
  - lời gọi khởi chạy của cửa sổ và của lượt ẩn có cùng tham số, chỉ khác `headless`: kênh `msedge`, profile trong thư mục riêng, sandbox, `HARDENING_ARGS`, hố đen, chặn service worker và tải xuống, preferences, route `**/*`, mock WebSocket, script gỡ WebRTC, profile bị xóa. Hạn khởi chạy theo hạn chung.
- Edge headless thật làm cửa sổ thay thế (11). Launcher của test dựng cùng `SessionBrowser` nhưng không truyền permit; không cửa sổ nào hiện ra.
  - Hạn chung hay shutdown tới ngay sau khi đọc trạng thái thật từ Edge: không lưu, phiên cũ giữ nguyên (thêm sau review M2b).
  - Đăng nhập fixture: form tự gửi mật khẩu mồi, 302 đặt cookie, trang chủ ghi localStorage và IndexedDB rồi báo sẵn sàng; `/api/me` của nguồn là bằng chứng. Kết quả: CONNECTED; lượt ẩn sau đó gửi đúng cookie và đọc được localStorage và IndexedDB; không lưu lại khi không đổi; TTL không kéo dài, hết ở giây 3.600. Bộ xác nhận không với được nguồn khác, host lạ hay http. Chỉ host của nguồn được tra DNS và nhận yêu cầu, qua địa chỉ công khai đã kiểm. Mật khẩu và cookie mồi không có trong kết quả, trạng thái, `repr` hay file DB.
  - Có cookie, URL mới và 200 nhưng nguồn chưa xác nhận: LOGIN_TIMEOUT; phiên cũ (generation 1) giữ nguyên.
  - Hạn chung cắt một trang đăng nhập đang tải (30 giây) sau 5 giây.
  - Trang kẹt trong một vòng lặp script làm lời gọi của bộ xác nhận treo: Edge của lượt bị kết thúc sau hạn + 1 giây (LOGIN_TIMEOUT) hay sau khi hủy + 1 giây (LOGIN_CANCELLED); không còn tiến trình Edge hay profile nào.
  - Đóng mọi trang, hay đóng cả trình duyệt: LOGIN_WINDOW_CLOSED, không lưu.
  - Hủy khi bộ xác nhận đang chạy rồi nó báo thành công muộn: LOGIN_CANCELLED, không lưu, manager từ chối kết quả muộn; trong lúc đó `status`/`statuses` trả lời ngay (dưới 2 giây).
  - Ngắt kết nối hay lượt mới chen vào giữa bằng chứng và lúc lưu: LOGIN_STALE, không lưu; lượt mới vẫn mở.
  - Manager khác (root khác) bắt đầu lượt cùng nguồn giữa chừng: mỗi manager từ chối lượt của bên kia.
  - Lỗi ghi kho phiên: SESSION_SAVE_FAILED, phiên cũ và file `session-1.bin` giữ nguyên.
  - Edge giữ các tùy chọn mật khẩu, tự điền và WebRTC khi đóng.
  - Mọi test kiểm không còn profile nào.
- Regression: xem mục 10.

**Mock và bằng chứng thật:**
- Bằng Edge headless thật: luồng đăng nhập, lưu, dùng lại phiên (cookie, localStorage, IndexedDB), lớp mạng M2a khi dùng qua coordinator, hủy, đóng, hạn chung, kết thúc Edge treo, chen ngang, lỗi lưu, Edge giữ tùy chọn profile.
- Chỉ bằng mock: cửa sổ có giao diện (runtime Playwright giả chỉ cho thấy tham số khởi chạy); các nhánh lỗi khởi chạy, profile, DB, luồng; thứ tự hủy và lượt mới (cửa sổ giả); `LoginView` khi không có Edge.
- Mock không phải bằng chứng cho cửa sổ thật.

**Còn chờ (chưa nghiệm thu):**
- Cửa sổ có giao diện thật: chưa mở lần nào. Người dùng cần kiểm, trên root tạm với fixture:
  - cửa sổ hiện ra và đóng đúng;
  - không có đề nghị lưu mật khẩu hay tự điền (test chỉ cho thấy Edge giữ các khóa tùy chọn);
  - lưu lượng nền của Edge có giao diện vẫn dừng ở hố đen;
  - lúc lưu, `storage_state` có mở thêm một trang nội bộ (nháy cửa sổ) hay không (review M2b, chưa kiểm).
  - Làm ở lượt kiểm riêng do người dùng chọn, hoặc ở M7.
- Nguồn thật: chưa biết bằng chứng đăng nhập, nên chưa có bộ xác nhận và đăng nhập bị từ chối (LOGIN_UNSUPPORTED). M3 thêm bộ xác nhận khi biết bằng chứng; M7 nghiệm thu cùng người dùng.
- Host của luồng đăng nhập thật (nhà cung cấp danh tính, khung thử thách) chưa biết. Redirect của fetch/XHR và 307/308 có thân vẫn bị từ chối (9.9), có thể làm hỏng luồng thật.

**Giới hạn:**
- Bộ xác nhận chạy `fetch` trong trang, nên script của chính nguồn có thể thay `fetch`. Nguồn là bên được tin trong phạm vi phiên của nó; script của host khác không tải được (M2a). Nếu M3 cần chắc hơn: gửi bằng `SessionHttp.send` với cookie của đúng URL lấy từ context.
- Hỏi mỗi giây: nếu người dùng đăng nhập xong rồi đóng cửa sổ ngay giữa hai lần hỏi thì không đọc được trạng thái (LOGIN_WINDOW_CLOSED); phải đăng nhập lại.
- Điểm commit là commit của manager. Một lần dừng tới sau đó (kể cả khi đang ghi file kho phiên, vì `end_login` chờ khóa của nguồn) không hủy phiên mới. Cửa sổ do người dùng đóng không phải lần dừng gửi tới manager: nếu cửa sổ đóng sau lần kiểm cuối mà trước commit thì phiên đã thu thập vẫn được lưu.
- Lượt khác tiến trình (hai Control Center cùng root) không dùng chung khóa trong bộ nhớ; khi đó chỉ còn compare-and-set của DB, và file của generation mới bị bỏ nếu commit thua (M1).
- Một yêu cầu đã vào handler trước hạn chỉ bị cắt khi bộ hẹn giờ chạy; phần dư tối đa là độ trễ của bộ hẹn giờ.
- Lời gọi Playwright treo chỉ dừng khi Edge bị kết thúc, nên lượt có thể kéo dài tới hạn + `grace_seconds` (10 giây). Việc tìm tiến trình dựa vào `--user-data-dir` trên dòng lệnh của Edge (psutil; tiến trình không đọc được thì bỏ qua). Lượt ẩn (`run_with_session`, M3) chưa có cơ chế này.
- `HeadedPermit` là quy ước trong code: code nào trong tiến trình cũng dựng được một permit; test chỉ giữ rằng trong `src/biliflow` chỉ coordinator dựng nó. Permit không tự kiểm lượt còn mở (coordinator kiểm).
- IndexedDB làm phiên lớn hơn. Kho phiên giới hạn 4 MiB mỗi phiên (M1); nguồn có IndexedDB lớn sẽ luôn lỗi SESSION_SAVE_FAILED. Kiểm với nguồn thật ở M7.
- Bộ xác nhận hỏi nguồn khoảng mỗi giây, tối đa 10 phút. Bộ xác nhận của nguồn thật (M3) nên xem URL hay chữ của trang trước và chỉ `fetch` khi cần.
- `LoginRun` chỉ nằm trong bộ nhớ. Khởi động lại giữa chừng: M1 đã xử lý (LOGIN_INTERRUPTED lúc khởi động); profile còn sót được `clear_browser_profiles` dọn (M2a).
- DB chỉ ghi LOGIN_FAILED cho các lỗi ngoài hủy, đóng và hết hạn; M4 quyết định cách hiện mã chi tiết của `LoginOutcome`.
- Trong lúc cửa sổ mở, `SessionBrowser.context` và trang là đối tượng Playwright thật; bộ xác nhận chỉ nhận `LoginView`.

### 9.12 Kết quả M3 (2026-10-08): đọc danh sách tập và lấy vé đúng file

Phạm vi: thư viện và test. Chưa nối hàng đợi, API, nhóm tác vụ hay giao diện (M4, M5). Không truy cập nguồn phim, không mở phiên thật, không đọc cấu hình local, DB bản chính hay biên bản riêng, không chạy Control Center, không cài gì. Chỉ đọc thêm `EPISODE-SELECTION.md` (được phép); không chép tên miền, URL hay tên phim nào vào repo.

**Tách trước khi thêm code:**
- `download_provider_config.py` (mô-đun lá): `PROVIDER_ID`, đọc `download_providers.local.json`, các helper hiển thị và `PUBLIC_PROVIDER_IDS`. `download_sources`, `download_account_config` và `download_account_vault` lấy từ đây; `RESERVED_IDS` của cấu hình tài khoản là `PUBLIC_PROVIDER_IDS` (test giữ nó bằng id của `direct` và `SITE_PROVIDERS`).
- `download_account_edge.py`: phần Edge (cờ, tùy chọn profile, môi trường, hố đen, tìm tiến trình). `download_account_browser.py` còn 620 dòng (trước 791), lớp kiểm mạng giữ nguyên; test M2 chỉ đổi chỗ import.
- `download_account_runs.py`: `run_with_session` chuyển ra đây, thêm phần của M3 (dưới).

**Đã implement:**
- Lượt ẩn (`run_with_session`):
  - Mỗi root + tài khoản Windows + nguồn một khóa riêng (`source_run_lock`). Không giữ khóa của worker, store hay manager trong lúc trình duyệt chạy. Chờ khóa thì hủy được ngay (Cancelled); chờ quá 15 phút: SourceBusy (SERVER_BUSY, thử lại được). Hai lượt cùng nguồn không bao giờ ghi đè cookie của nhau (L3 của 9.9).
  - Một hạn chung cho cả lượt (mặc định 180 giây, tối đa 600): là `stop_at` của trình duyệt. Tới hạn hay khi hủy, control riêng của lượt dừng; `grace_seconds` (10) sau đó Edge của đúng profile của lượt bị kết thúc (`force_close`), không bao giờ trình duyệt cá nhân. Hết hạn: RunTimedOut (NETWORK, thử lại được).
  - Luôn headless (`headed`, `stop_at` truyền vào bị từ chối); không mở cửa sổ đăng nhập. `hosts`: trình duyệt của provider chỉ tới host portal và tickets, không bao giờ tới máy chủ file.
  - Cookie xoay vòng lưu qua `save_rotated` với đúng lease (generation; không đổi `authenticated_at`, TTL 3.600 giây). Lượt kết thúc bằng lỗi, hủy hay hết hạn vẫn đọc trạng thái một lần nếu trình duyệt còn trả lời và lưu như trên (theo review M3).
  - Sau khi trình duyệt đóng, hủy và hạn được kiểm lại cả khi action đã trả: lượt bị dừng hay quá hạn lúc đọc trạng thái hay lúc đóng không trả giá trị, nhưng cookie xoay vẫn được lưu (mục 9.13).
- Danh sách (`download_account_listing.py`, không trình duyệt, không mạng):
  - Danh tính: nguồn + phim + tập + bản, đều là id của nguồn (`[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`), không bao giờ suy từ tiêu đề, dung lượng hay link. `FileSelection.key` = `acct-` + 40 ký tự hex SHA-256 của bốn id.
  - Thứ tự: mùa theo số; tập theo số trong mùa (2 trước 10); mục không số giữ chỗ của nguồn, mục có số lấp các chỗ có số theo thứ tự số. Tập đặc biệt: chỗ nguồn ghi (`after` N, 0 là đầu mùa); không ghi (hay ghi số không có) thì vào nhóm "Đặc biệt" sau mọi mùa, theo thứ tự nguồn. Đây là lựa chọn cho điểm 2 của 9.10, theo prompt M3.
  - Trailer, quảng cáo: loại theo vai trò cấu trúc mà bộ đọc thấy (`role`), đếm trong `skipped`; không bao giờ "lấy video đầu tiên".
  - Bản (variant) là file của một tập, không phải tập. Một id hai nội dung: CONFLICTING_ITEMS; trùng y hệt: đếm "duplicate".
  - `complete=false` kèm lý do: PAGE_LIMIT, ITEM_LIMIT (1.000 tập, 5.000 file), TIME_LIMIT, PAGE_FAILED, PAGE_SIGNED_OUT, PAGE_NOT_FOLLOWED (link trang không được đi theo), PARTIAL_READ (dừng ở trang có file đã chọn), MORE_NOT_LINKED, OTHER_FILM, UNREADABLE_ITEMS (thiếu id hay cách lấy vé), CONFLICTING_ITEMS. Một trang không bao giờ được coi là cả bộ.
  - `public()`: id, nhãn, số, thứ tự, dung lượng trang ghi, loại bản, `fingerprint`; không link trang, cách bấm, vé, link ký số, cookie hay storage_state. Phần riêng (`navigation`: trang và cách bấm của mỗi file) chỉ ở trong bộ nhớ.
  - `plan_selection(listing, mode, episodes, variant_kind | variants)`: "all" (Tải tất cả các tập đang có) hoặc "pick" (Chọn tập); một loại bản cho mọi tập (chất lượng|âm thanh) hoặc một bản cho từng tập. Tập thiếu bản: `missing`, không thay bằng bản khác; hai file cùng loại: `ambiguous`, không lấy. Không có mode, chọn rỗng hay tập lạ: ValueError (không bao giờ tự chọn cả bộ). Nút: "Tải N tập", hoặc "Tải N tập đã thấy" khi danh sách chưa đủ; `note` khi đó nói rõ chỉ gồm các tập đã thấy (mục 9.13).
  - `film_choices`: lựa chọn của phim lẻ (đường NEEDS_CHOICE có sẵn); danh sách chưa đủ thì mọi nhãn có thêm "danh sách bản chưa đủ".
- Trang và vé (`download_account_pages.py`):
  - `SourcePageReader`: `film_page`, `signed_out`, `ticket_page`, chỉ đọc qua `PageView` (URL, đọc có giới hạn thuộc tính và chữ của phần tử; không bấm, không script, không route). `PAGE_READERS = {}`: chưa nguồn thật nào có bộ đọc. Test có bộ đọc fixture cho cấu trúc trang tự làm.
  - `read_listing`: theo link trang mà bộ đọc trả (vùng phân trang), chỉ https:443, cùng host với trang đã dán, không bao giờ trang đăng nhập, bỏ fragment; nghỉ 0,3 giây giữa hai trang; tối đa 40 trang và chừa 15 giây của lượt; link gặp lại chỉ đọc một lần; link trang bộ đọc trả mà không được đi theo làm danh sách chưa đủ (PAGE_NOT_FOLLOWED, mục 9.13). Trang đầu lỗi: mã riêng (UNAVAILABLE, FORBIDDEN, SERVER_BUSY, NOT_A_FILM_PAGE, NETWORK); trang sau lỗi hay chuyển sang trang đăng nhập: danh sách chưa đủ. Không xin vé, không tải file.
  - `get_ticket`: chỉ bấm khi lượt còn đủ thời gian (ít nhất min(thời gian chờ vé, 10 giây)), để vé không bị tạo rồi bỏ; chờ vé dừng 5 giây trước hạn của lượt để còn đọc trạng thái và đóng trình duyệt. Bấm đúng nút của file đã chọn trên trang có file đó. Trang vé là trang trên host tickets hay portal mà bộ đọc nhận là vé của đúng tập và đúng file. Trang trên host khác (quảng cáo) bị đóng ngay; trang vé của file khác không bao giờ được dùng. Chờ đúng thời gian của trang; challenge: SOURCE_CHALLENGE (không vượt); tab đóng: TICKET_TAB_CLOSED; không thấy vé: TICKET_NOT_OPENED; hết thời gian: TICKET_TIMEOUT; trang vé không còn hiện vé quá 2 giây: TICKET_FAILED. Link của nút tải chỉ được đọc (không đi theo) và phải là https:443 trên host files (TICKET_LINK_REFUSED).
  - Bằng chứng đăng xuất: trang đăng nhập (host, path và mọi query item của `login_url`) hay bộ đọc báo đăng xuất, trên trang đã dán hay trang vé. 401/403 không có bằng chứng: FORBIDDEN; mạng, DNS: NETWORK; 429/5xx: SERVER_BUSY. Không mã nào trong số đó là đăng nhập.
- Provider (`download_account_sources.py`, `AccountSourceProvider`, id = id nguồn):
  - `claims` mọi host của nguồn. Link vé hay link file: ACCOUNT_PAGE_ONLY. Chưa có manager (M4 truyền vào): ACCOUNT_NOT_READY. Chưa có bộ đọc: READER_UNSUPPORTED. Không bao giờ SourceDeclined (đường yt-dlp).
  - `resolve` không có lựa chọn (thăm dò trang đã dán): đọc cả danh sách. Phim nhiều tập: `SourceNeedsEpisodes` (mã NEEDS_EPISODES, `.listing` là `public()`). Phim nhiều bản, hay danh sách chưa đủ: `SourceNeedsChoice`. Phim một file và danh sách đủ: lấy vé luôn. Không có file nào: NO_FILES. Không xin vé khi còn chờ lựa chọn.
  - `resolve` có lựa chọn (`{"selection": key}` sau lựa chọn phim lẻ; `{"account_file": ids}` cho tác vụ một tập; identity đã lưu khi refresh): đọc tới khi thấy đúng file; khác phim hay không còn mục: SOURCE_CHANGED; danh sách chưa đủ mà chưa thấy: ITEM_NOT_FOUND. Rồi xin vé của đúng file đó.
  - Vé hết hạn, máy chủ file từ chối ngay vé mới (401/403), phiên đổi trong lượt: xin lại, tối đa 2 vé mỗi lần resolve (TICKET_EXPIRED, TICKET_REFUSED, SESSION_CHANGED).
  - Thăm dò: `resolve_file` qua `ctx.http` (SafeHttp không cookie: GET có Range và giới hạn, kiểm `Content-Range`, container và luồng bằng code có sẵn; không dùng HEAD). MKV giữ MKV. Identity `{"kind": "account-file", source, film, episode, variant, version, size}` (không vé, token hay tiêu đề); kế hoạch `strict_versions=True`.
  - `SourceLoginRequired` (mã SOURCE_LOGIN_REQUIRED, khác LOGIN_REQUIRED của 401 máy chủ file; mang `source_id`, `generation`, `reason`): NOT_CONNECTED, SESSION_EXPIRED, SESSION_REJECTED… Chỉ bằng chứng rõ mới `mark_invalid`, và chỉ generation của lượt; bằng chứng về phiên đã được thay (đăng nhập mới, ngắt kết nối giữa lượt) thì xin lại với phiên hiện tại; vé của phiên cũ bị bỏ.
  - `discover(url, control)`: chỉ danh sách, không vé.
- `download_media_file.py`: `FilePlan.strict_versions` (chỉ provider tài khoản bật). Phần đã tải chỉ được nối khi link mới cho đúng validator đã lưu (gửi If-Range); khác hay thiếu thì xóa phần và tải từ byte 0 trước mọi yêu cầu; không validator thì mỗi lần thử lại đều từ 0 và số lần thử không được đặt lại. Với plan nghiêm ngặt, 401 cũng xin nguồn mới, và một 206 có dấu phiên bản khác phần đã tải không bao giờ được nối (mục 9.13). Đường của provider khác giữ nguyên (test hồi quy). `run` tách hai helper (`_resume_offset`, `_same_version`).
- `download_sources.default_registry`: thêm một provider tài khoản cho mỗi nguồn đã cấu hình, đứng trước `direct` và provider trang. `download_account_config`: host files không được trùng portal hay tickets.

**Hợp đồng cho M4/M5:**
- Tác vụ dán trang phim: thăm dò = `provider.resolve(url, ctx)` không có `previous`.
  - Kết quả là `ResolvedSource`: đi đường phim lẻ hiện có.
  - `SourceNeedsChoice`: NEEDS_CHOICE hiện có; người dùng chọn thì `previous = {"selection": key}`.
  - `SourceNeedsEpisodes`: M4 lưu `.listing` (chỉ `public()`), M5 hiện hai chế độ. Khi người dùng bấm "Tải N tập", M4 gọi `discover` để có `Listing` mới, so `fingerprint` với danh sách đã hiện (khác thì hiện lại), rồi `plan_selection`. Mỗi `FileSelection` thành một tác vụ con có batch id, theo thứ tự mùa → tập; chặn trùng bằng `FileSelection.key`.
  - `SourceLoginRequired`: WAITING_LOGIN theo **loại lỗi** (không theo chuỗi mã), giữ `source_id` và `generation`, không giữ slot. Lỗi khác giữ mã riêng.
- Tác vụ con: `ctx.previous = {"account_file": FileSelection.public()}` lúc thăm dò; lúc tải, worker hiện có truyền `identity_detail` đã lưu và so `identity_key` (version, size): khác là SOURCE_CHANGED. Vé chỉ được xin trong resolve ngay trước khi dùng slot. Mọi thao tác trình duyệt của một nguồn đã tuần tự (`source_run_lock`).
- Không lưu link, vé, `navigation` hay trigger vào DB, sự kiện hay log.

**Test (2026-10-08, chạy trong worktree, `BILIFLOW_FFMPEG` của bản cài):**
- `tests/test_download_account_sources.py`: 35 test, OK. 31 chạy trên **Edge headless thật** với trang phim, trang vé, quảng cáo và máy chủ file tự làm (host `.example`, CA tạm, lớp mạng M2a, phiên giả trong kho phiên test); file là clip MKV tự tạo bằng FFmpeg. Gồm: bộ 16 tập hai mùa ba trang có trailer và quảng cáo (không vé nào lúc đọc), tập 2/10, tập đặc biệt và tập không số, tập thiếu bản, vòng phân trang và mục trùng, PAGE_LIMIT, MORE_NOT_LINKED, OTHER_FILM, PAGE_FAILED, trang đầu 404 hay không phải trang phim, phim lẻ một bản và nhiều bản, phim có danh sách chưa đủ, trang không có file; vé chỉ của file đã chọn (popup quảng cáo bị bỏ), thời gian chờ của trang vé được giữ, vé hết hạn một lần rồi được, luôn hết hạn, vé của tập khác cùng mã bản, tab tự đóng, gone, challenge, error, file bị xóa, file bị mất khỏi danh sách, phim khác, máy chủ file từ chối vé mới; thiếu phiên hay phiên hết hạn (không mở Edge), trang đăng nhập, thông báo đăng xuất, trang vé chuyển sang đăng nhập, 403/503/DNS không phải đăng nhập, cookie xoay vòng không kéo dài phiên, hai lượt cùng nguồn (cookie xoay mỗi lần dùng) không ghi đè nhau, ngắt kết nối và đăng nhập mới giữa lượt, hủy lúc chờ khóa, hết hạn với trang kẹt trong script (Edge và profile bị dọn), hủy lúc chờ vé, cookie xoay trước khi hủy vẫn được lưu, không bấm lấy vé khi lượt hết thời gian; vé hết hạn giữa lúc tải được thay và tải tiếp đúng phiên bản (Range + If-Range), file đổi cùng dung lượng thì tải lại từ đầu, phiên hết hạn không dừng file đang tải nhưng vé mới cần đăng nhập (phần đã tải được giữ); bait cookie và token không có trong public, repr, lỗi, log, DB hay file tạm. 4 test không cần trình duyệt: registry (link portal, vé, file của nguồn tài khoản không sang `direct`/yt-dlp), registry bộ đọc và bộ xác nhận thật vẫn trống, id dành riêng, giới hạn của lượt ẩn.
- `tests/test_download_account_listing.py`: 33 test, OK, không trình duyệt (trình duyệt giả và bộ đọc giả): thứ tự, đặc biệt, kiểm id/số/dung lượng/nhãn/trigger, trailer, trùng và xung đột, giới hạn, `public()` và `fingerprint`, `FileSelection`, `plan_selection`, `film_choices`, đi qua các trang (link ngoài host, trang đăng nhập có query, trang sau bị đăng xuất, trang không có id phim, giới hạn trang và thời gian, hủy).
- `tests/test_download_account_transfer.py`: 8 test, OK, không trình duyệt (máy chủ fixture, clip tự tạo): vé mới cùng validator thì nối; vé của phiên bản khác thì tải lại trước mọi Range (máy chủ bỏ qua If-Range); hồi quy cho thấy đường thường vẫn như cũ; không validator thì mỗi lần thử từ 0, số lần có giới hạn, lượt sau cũng từ 0; refresh giữa lúc tải: cùng phiên bản thì nối, khác thì từ 0; 401 chỉ xin nguồn mới ở đường nghiêm ngặt.
- Cấu hình tài khoản: thêm hai trường hợp host files trùng portal hay tickets.
- Toàn bộ `test_download*` cùng `tests.test_cache_dependencies` và `tests.test_stage_cache`: xem nhật ký ở mục 10.

**Chỉ là giả lập (không phải bằng chứng cho nguồn thật):** cấu trúc trang, bộ đọc fixture, trang vé, quảng cáo, máy chủ file, phiên và Windows DPAPI giả của kho phiên test. Edge thật chỉ chứng minh luồng của BiliFlow (lớp mạng, popup, chờ, hủy, hạn) trên trang tự làm.

**Nguồn thật chưa kiểm (còn thiếu để có bộ đọc và bộ xác nhận thật, M7):**
- cấu trúc trang phim thật: vùng tải, id phim/mùa/tập/bản, phân trang hay danh sách mùa, chỗ của tập đặc biệt, trailer và quảng cáo nằm ở đâu;
- bằng chứng đăng nhập và đăng xuất của trang;
- trang vé: mở bằng popup hay cùng tab, id nào được hiện, thời gian chờ, hạn và hạn mức của vé, quảng cáo hay challenge đi kèm;
- máy chủ file: có cần cookie hay Referer không (BiliFlow không gửi; nếu cần thì nguồn đó chưa tải được: FILE_NOT_VIDEO hay TICKET_REFUSED), có ETag hay Last-Modified ổn định giữa các vé không (không có thì luôn tải lại từ đầu), Range có hoạt động không.
- Cần người dùng cho một lượt kiểm riêng với trang của họ (root tạm, Control Center thử).

**Giới hạn:**
- File được tải không có phiên. Nguồn mà máy chủ file đòi cookie thì chưa tải được (báo lỗi, không gửi phiên).
- Plan nghiêm ngặt không có validator: mỗi lần thử lại đều từ byte 0 (có giới hạn số lần).
- Dung lượng trang ghi không được so với dung lượng thăm dò (trang có thể làm tròn); file đổi được bắt bằng `version` của trang (nếu có), dung lượng và validator.
- Thứ tự của mục không số theo thứ tự đọc trang; phân trang kiểu "1 2 3 … 10" có thể làm đảo thứ tự mục không số giữa các trang (mục có số không bị ảnh hưởng).
- Bộ đọc không nói loại mà danh sách chỉ có một tập không mùa thì coi là phim lẻ; danh sách chưa đủ thì vẫn bắt người dùng chọn.
- Việc lưu cookie thất bại (ví dụ phiên quá 4 MiB) chưa được báo ra (`SessionRun.saved`); M4 quyết định cách hiện.
- `discover` trả `Listing` có phần riêng `navigation` (repr ẩn); M4 chỉ được lưu `public()`.
- `run_options` và `reader` của provider là chỗ tiêm của test (như `network` của M2a); code production không truyền.
- Nguồn bị bỏ vì lỗi cấu hình không còn là nguồn tài khoản: link của nó đi đường thường (yt-dlp, không phiên), `account_problems` của registry nói lý do. M4 cần hiện lỗi đó lúc dán link.
- Bộ đọc đọc trong main world của trang; script của trang có thể làm sai giá trị đọc được, nhưng link vé luôn được kiểm lại (https:443, host files) và trang vé phải đúng host.
- Bộ đọc chọn link trang và cách bấm; phiên đi theo mọi link trang nó trả (chỉ trên cùng host, không trang đăng nhập); link nó trả mà không được đi theo làm danh sách chưa đủ.

### 9.13 Sửa sau review M3 của Codex (2026-10-08)

Codex tái hiện ba lỗi P2 bằng bốn test độc lập (file ngoài repo, chỉ cửa sổ/HTTP giả và byte tự tạo trong root tạm). Chạy lại trước khi sửa: 4 FAIL (0,302 giây), đúng ba lỗi đó. Không có checkout khác: mọi sửa đổi ở worktree này, branch `feat/download-source-accounts`.

**1. Lượt ẩn hết hạn hay bị hủy lúc đọc trạng thái vẫn trả kết quả.**
- Nguyên nhân: `run_with_session` chỉ đổi hết hạn/hủy thành RunTimedOut/Cancelled ở nhánh exception. Khi action đã trả mà `storage_state()` hay lúc đóng trình duyệt kéo tới hạn (hoặc người gọi hủy đúng lúc đó), nhánh trả bình thường không kiểm lại và trả `SessionRun`. Kiểm hủy của provider sau lượt chỉ thấy control của người gọi, không thấy hạn của lượt.
- Sửa (`download_account_runs.py`): `_stopped` được kiểm sau khi trình duyệt đóng, ở cả nhánh trả bình thường lẫn nhánh lỗi. Hủy của người gọi: Cancelled. Control riêng của lượt đã dừng, hoặc `time.monotonic()` đã qua hạn (kể cả khi timer chưa kịp chạy): RunTimedOut. Nhánh bình thường vẫn lưu cookie xoay trước (`save_rotated`, cùng lease, không đổi `authenticated_at` hay TTL), rồi mới báo lỗi. Danh sách hay vé của lượt đã dừng không bao giờ được trả.
- Kèm theo (`download_account_pages.py`): `get_ticket` chừa `FINISH_RESERVE_SECONDS` = 5 giây cuối của lượt (trước là 1 giây) cho việc đọc trạng thái và đóng trình duyệt, để một vé lấy sát hạn không bị bỏ vì lượt quá hạn lúc đóng. Điều kiện không bấm khi còn dưới min(thời gian chờ vé, 10 giây) giữ nguyên.
- Test mới `tests/test_download_account_runs.py` (7, trình duyệt giả, không Edge):
  - hết hạn và hủy trong action (action trả về hay ném Cancelled), trong lúc đọc trạng thái và trong lúc đóng;
  - hạn vẫn được tính khi timer không chạy;
  - lượt đúng hạn vẫn trả giá trị;
  - mọi trường hợp đều lưu cookie xoay, `authenticated_at` và `recheck_at` không đổi.

  Vá tạm `_stopped` thành không làm gì (chỉ trong bộ nhớ) thì 6/7 test FAIL; chỉ còn lượt đúng hạn đạt.

**2. Phân trang bị bỏ mà danh sách báo đầy đủ.**
- Nguyên nhân: `read_listing` bỏ qua im lặng link trang mà `_list_link` từ chối (host portal khác, http, cổng khác, host ngoài nguồn, trang đăng nhập). Test cũ `test_page_links_are_followed_once_inside_the_portal_only` còn mong `complete=True` dù có link phân trang bị bỏ.
- Sửa (`download_account_pages.py`, `download_account_listing.py`):
  - Link bộ đọc khai báo là trang của danh sách mà không được đi theo thì danh sách chưa đủ, lý do PAGE_NOT_FOLLOWED. Chính sách cùng host giữ nguyên.
  - Link về trang đã đọc hay về chính trang đó (fragment) không tính.
  - Đường lấy vé dừng sớm bằng `stop` mà còn trang chưa đọc: PARTIAL_READ. Link của trang hiện tại nay được xếp hàng trước khi kiểm `stop`; trình duyệt vẫn đứng ở trang đó.
  - `SelectionPlan.note`: danh sách chưa đủ thì nói rõ lựa chọn chỉ gồm N tập trong phần đã thấy (cùng nút "Tải N tập đã thấy"); danh sách đủ thì để trống.
  - Hợp đồng của bộ đọc: chỉ trả link của vùng phân trang, đã lọc quảng cáo; link nào nó trả mà phiên không đi theo thì danh sách chưa đủ.
- Test (`tests/test_download_account_listing.py` 35 thay cho 33; `tests/test_download_account_sources.py` 35 với thêm một ca Edge):
  - Test cũ nay mong `complete=False` với PAGE_NOT_FOLLOWED, và từng loại link bị từ chối đều cho lý do này.
  - Backlink, fragment và link đã đọc giữ `complete=True`.
  - Trường hợp của Codex: trang 2 trên host portal thứ hai không được đọc, danh sách chưa đủ, và lựa chọn "all" nói rõ chỉ gồm 1 tập đã thấy.
  - Dừng sớm còn trang chưa đọc thì có PARTIAL_READ; đã đọc hết thì không.
  - Trên Edge thật: link trang ngoài host thì PAGE_NOT_FOLLOWED.

**3. Tải nối nối phản hồi 206 của phiên bản khác.**
- Nguyên nhân: đường `strict_versions` chỉ so validator lúc thăm dò link. `_receive` nhận 206 chỉ kiểm Content-Range và tổng dung lượng, nên máy chủ bỏ qua If-Range mà trả 206 với ETag mới thì phần cũ bị nối với byte của bản mới (`AAABBB`).
- Sửa (`download_media_file.py`): trước khi ghi byte nào của một 206 (plan nghiêm ngặt, offset > 0), so dấu phiên bản của chính phản hồi với validator của phần đã tải (`other_version`).
  - Một dấu bằng đúng validator (ETag hay Last-Modified) là cùng bản.
  - Validator là ETag: so với ETag của phản hồi. ETag yếu mà tag khác cũng là bản khác; ETag yếu cùng tag không chứng minh gì.
  - Validator là Last-Modified: so với Last-Modified của phản hồi.
  - Dấu khác loại hoặc không có dấu: không chứng minh gì; If-Range của yêu cầu quyết định như mọi máy chủ.
  - Khác bản: không nối, xóa phần đã tải và tải lại từ byte 0, tối đa một lần mỗi lượt (`MAX_VERSION_RESTARTS = 1`). Lần thứ hai trong cùng lượt: SOURCE_CHANGED. Từ M6 (mục 9.19), một lần làm mới vé mà link mới có validator khác phần đã tải cũng tính vào cùng bộ đếm này (trước đó nó tải lại từ 0 mà không đếm).
  - Cũng từ M6, ở mọi đường: một lần thử chỉ đếm lại `FILE_RETRIES` khi phần tải dài hơn phần dài nhất đã giữ trong lượt; mốc đó về 0 khi phần bị bỏ vì khác bản, kể cả khi một câu trả lời 200 cả file có dấu cho thấy khác bản (tối đa `MAX_NEW_VERSION_RESETS = 1` lần mỗi lượt, không tính vào `MAX_VERSION_RESTARTS`). Khi tải nối, câu trả lời 2xx khác 200 và 206 dừng BAD_RESPONSE, không ghi byte nào.
  - Validator không bao giờ bị đổi cho khớp với phản hồi. Chỉ khi phần bắt đầu từ byte 0 thì lấy validator của chính phản hồi đó (cả phần là bản đó).
  - Đường của provider thường không đổi.
- Test (`tests/test_download_account_transfer.py` 14 thay cho 8; máy chủ fixture, clip tự tạo):
  - bảng `other_version`: 16 trường hợp (gồm Last-Modified không theo RFC đi cùng một ETag mà lúc thăm dò không lấy);
  - lượt sau nhận 206 có ETag khác: không nối, tải lại cả bản mới;
  - lần thứ hai trong một lượt: SOURCE_CHANGED, phần chỉ giữ byte của bản trước, và If-Range của lần nối là ETag của lần tải từ đầu;
  - thử lại trong cùng lượt: ETag khớp, ETag yếu cùng tag hay không có dấu thì nối; ETag khác hay ETag yếu khác tag thì tải lại;
  - 200 gửi cả file: tải lại từ đầu;
  - phần có Last-Modified: cùng ngày thì nối, khác ngày thì tải lại, ETag không được so với ngày.

  Vá tạm `other_version` thành luôn False thì đúng 5 trường hợp khác bản FAIL.

**Review chỉ đọc sau khi sửa (một agent, đúng đắn và bảo mật):** không có CRITICAL hay HIGH, không lộ bí mật mới. Đã sửa ba điểm LOW:
- test hủy trong action dùng lại control người gọi đã hủy, nên ca "ném Cancelled" bị chặn ở bước chờ khóa và không bao giờ chạy tới action. Nay mỗi ca có control mới và test kiểm rằng action đã chạy; tương tự cho test lúc đóng;
- `other_version` coi một Last-Modified không theo RFC là ETag, nên một 206 mang ETag mà lúc thăm dò không lấy (yếu, hay không có) bị coi là bản khác dù Last-Modified y hệt. Nay một dấu bằng đúng validator luôn là cùng bản;
- comment cũ ở test không bấm vé khi lượt thiếu thời gian.

Hai điểm LOW còn lại được ghi vào giới hạn bên dưới.

**Kết quả:**
- 4 test độc lập của Codex: trước khi sửa 4 FAIL; sau khi sửa 4/4 OK (0,310 giây ở lượt cuối).
- Test của các chỗ sửa: `test_download_account_runs` 7, `test_download_account_listing` 35, `test_download_account_transfer` 14: OK.
- Toàn bộ `test_download*` (có `BILIFLOW_FFMPEG`; gồm M1 tài khoản, kho phiên và phạm vi; M2 trình duyệt, HTTP và đăng nhập với hai regression M2b; M3 nguồn tài khoản trên Edge headless; provider, dispatch, worker, API) cùng `tests.test_cache_dependencies` và `tests.test_stage_cache`, 790 test:
  - lượt 1 (trước ba điểm LOW của review): 790 OK, không skip, 693 giây;
  - lượt 2 (code cuối): 789 OK, 1 FAIL. Test bị FAIL là `SessionTest.test_the_runs_deadline_ends_a_page_stuck_in_a_script`: nó treo khoảng 70 phút (xem phát hiện dưới), rồi kết thúc bằng RunTimedOut đúng như thiết kế nhưng quá mốc 25 giây;
  - chạy riêng test đó 8 lần trên code cuối, có watchdog: 8/8 OK (9,3–9,8 giây).
- `git diff --check` sạch (chỉ có cảnh báo LF/CRLF của `.gitignore`). Các file sửa chỉ có host `.example`. Sau test không còn tiến trình Edge hay driver nào của test.

**Phát hiện mới ở lượt 2 (không thuộc ba lỗi trên):**
- Hiện tượng: test trang kẹt trong script treo khoảng 70 phút. Edge của lượt đã bị `force_close` kết thúc (không còn tiến trình msedge nào của test), nhưng một lời gọi Playwright trong `_run_browser` (action, đọc trạng thái hoặc đóng) vẫn chờ mãi driver Playwright (node.exe) của chính lượt đó.
- Kết thúc riêng node.exe đó thì lượt dừng ngay bằng RunTimedOut. Lần đó không có stack. Không tái hiện được khi chạy riêng (8/8 OK).
- Đã sửa trong một lượt riêng (prompt sửa treo M3, 2026-10-08), xem phần dưới.

**Sửa treo driver Playwright sau khi Edge đóng (2026-10-08).**

Nguyên nhân đã xác minh (đọc Playwright 1.63 đã cài, một spike và các test bên dưới):
- API sync gửi mọi lời gọi qua pipe tới driver `node.exe`. Driver này do chính `sync_playwright().start()` của lượt sinh ra, là tiến trình con của BiliFlow.
- Một lời gọi đang chờ chỉ được giải phóng khi driver trả lời hoặc pipe đóng. `runtime.stop()` còn chờ driver thoát (`communicate()`).
- Driver không nhận ra Edge đã bị kết thúc thì lời gọi đang chờ (action, đọc trạng thái), `context.close()` và `runtime.stop()` đều chờ mãi. `force_close` chỉ kết thúc Edge nên không đủ.
- Kết thúc driver qua handle của lượt thì pipe đóng: lời gọi đang chờ ném lỗi ngay (`Exception("Connection closed…")`, không phải PlaywrightError), và `stop()` trả về trong dưới 5 giây.
- Tái hiện có chủ đích: tạm dừng (suspend) đúng driver của test. Mọi lời gọi khi đó chờ y như với một driver không thấy Edge đóng.

Cách sửa:
- `download_account_driver.py` (mới, 137 dòng):
  - `DriverProcess`: ghi driver của lượt và chỉ kết thúc nó qua handle;
  - `blocked_at`: đọc chỗ lượt đang chờ;
  - `DriverHang(phase, stack)`.
- `download_account_browser.py`:
  - ghi driver ngay sau `start()`. Không xác định được driver thì không mở Edge: BrowserUnavailable "DriverUnknown" (fail closed, vì lượt đó không có cách kết thúc khi treo);
  - giai đoạn `open`, `page`, `storage_state`, `close-context`, `close-runtime`, `closed`;
  - các lệnh cài đặt sau khi launch (route, init script, timeout) nằm trong cùng `try` với launch;
  - `end_driver()`: gọi được từ luồng khác, không gọi API Playwright nào. Nó ghi `hang` ngay trước khi kill: lời gọi được giải phóng có thể kết thúc lượt ngay, nên `hang` phải có trước. Kill xong thì kết thúc Edge của profile thêm một lần, cho Edge khởi động chậm sau lần kết thúc đầu.
  - Trình duyệt chỉ xong khi `close()` đã chạy hết, kể cả xóa profile. `_closed=True` lúc bắt đầu đóng không tắt bước kết thúc driver.
- `download_account_runs.py`, watchdog hai bước:
  - lần dừng đầu (hạn hay hủy; lần sau không gài thêm) → sau một grace `force_close` (Edge của profile lượt) → sau thêm một grace `end_driver`, nếu trình duyệt vẫn chưa đóng;
  - timer còn gài tới khi `_run_browser` trả về (sau cleanup), rồi mới bị hủy;
  - lỗi vẫn là RunTimedOut hay Cancelled đúng lý do như trước, thêm `hang` khi đã phải kết thúc driver;
  - không bao giờ trả danh sách hay vé muộn; khóa nguồn luôn được nhả ở `finally`.
- `download_account_login.py`: cửa sổ đăng nhập dùng chung vòng đời trình duyệt nên có cùng hai bước.
  - Timer gắn với `LoginRun`, bị hủy khi lượt xong; có `LoginOutcome.hang`.
  - Lời gọi được giải phóng khi driver bị kết thúc ném một `Exception` thường. Đã có lệnh dừng thì `_open_window` trả mã của lệnh đó (LOGIN_TIMEOUT hay LOGIN_CANCELLED); chưa có thì lỗi đi tiếp như trước. Bộ xác nhận ném lỗi lúc đã có lệnh dừng cũng trả mã dừng thay cho VERIFIER_ERROR.
  - Không đăng nhập thật.

Cách xác định driver của đúng lượt:
- Ghi `Popen` mà kết nối của chính `sync_playwright()` của trình duyệt này giữ (`_connection._transport._proc._transport._proc`), ngay sau `start()`.
  - Lưu cùng `pid`, thời điểm tạo (`create_time` của psutil) và đường dẫn exe.
  - Chỉ ghi khi tiến trình còn chạy và là con trực tiếp của tiến trình BiliFlow (ppid = `os.getpid()`).
- Trước khi kết thúc, kiểm lại: handle vẫn chỉ đúng pid đó, chưa thoát (kể cả theo `poll()` của chính handle), cùng thời điểm tạo, cùng exe, vẫn là con của BiliFlow. Sai một điều thì không làm gì và không ghi `hang`.
- Kết thúc bằng `Popen.kill()` (TerminateProcess trên handle của chính lượt). Handle còn mở thì Windows không cấp lại pid đó; thời điểm tạo chặn thêm trường hợp PID tái dùng.
- Không bao giờ tìm theo tên `node.exe` hay theo đường dẫn driver dùng chung. Không đụng driver của nguồn khác, Claude/Codex, dev server hay trình duyệt cá nhân.

Chẩn đoán an toàn:
- `hang.phase` cho biết lượt đang ở giai đoạn nào.
- `hang.stack` chỉ có `module:hàm:dòng`: khung trong cùng (thường là `playwright._impl._sync_base:_sync`, lúc runtime dừng là `playwright._impl._connection:stop_sync`) và tối đa 8 khung của `biliflow.*`. Greenlet của lượt đang chạy (không bị treo ở chỗ chuyển fiber) thì đọc khung hiện tại của chính luồng đó.
- Không có biến cục bộ, body, cookie, vé, URL ký hay text của exception.

Giới hạn thời gian thật:
- Lượt ẩn kết thúc tối đa sau khoảng:
  - `run_seconds` + 2 × `grace_seconds`;
  - cộng thời gian kết thúc Edge: thường dưới 1 giây, tối đa `KILL_WAIT_SECONDS` = 10 giây cho mỗi tiến trình Edge không chịu thoát;
  - cộng `DRIVER_WAIT_SECONDS` = 5 giây cho driver;
  - cộng xóa profile: 3 lần thử, nghỉ 0,1 rồi 0,2 giây; không xóa được thì `profile_left`.
- Với mặc định (180 + 2 × 10 giây), lượt ẩn kết thúc trong khoảng 206 giây khi Edge thoát bình thường.
- Thời gian chờ khóa nguồn (tối đa 15 phút, hủy được) tính trước mốc đó.
- Đăng nhập: hạn của lượt (mặc định 600 giây) hoặc lúc hủy, cộng 2 × `grace_seconds` (mặc định 10 giây), cộng các phần như trên.

Test (mọi lần chờ đều có giới hạn; treo thì FAIL, không làm treo cả bộ test):
- `tests/test_download_account_driver.py`, 12 test. Chỉ dùng tiến trình con của chính test: Python ngủ, cô lập, không cửa sổ, chạy trong thư mục tạm, và driver của một runtime test tự mở.
  - Driver được kết thúc qua handle của nó. Phần báo cáo (`before`) chạy khi driver còn sống, ngay trước khi kill.
  - Bị từ chối, không báo cáo gì: PID tái dùng (thời điểm tạo khác), exe khác, handle của tiến trình khác, tiến trình không phải con của BiliFlow (handle giả, không bao giờ gọi `kill`), driver đã thoát sau khi được ghi. Tiến trình đã thoát thì không ghi.
  - `blocked_at` từ luồng khác (greenlet bị treo, hoặc khung hiện tại của luồng) chỉ cho `module:hàm:dòng`, không có giá trị biến.
  - Playwright đã cài: handle là `node.exe` con của tiến trình test; lời gọi chờ driver đã tạm dừng kết thúc khi driver đó bị kết thúc, `stop()` dưới 5 giây.
- `tests/test_download_account_hang.py`, 8 test trên Edge headless thật.
  - Môi trường: fixture server, không mạng, root tạm. Lượt 8 giây (để Edge kịp khởi động trước khi driver bị tạm dừng), grace 1 giây; mỗi lượt có watchdog ngoài 60 giây.
  - Driver ngừng trả lời ở các giai đoạn: trước khi Edge chạy, sau khi Edge chạy (lúc gài route), trong action, lúc đọc trạng thái, lúc đóng context, lúc runtime dừng. Kết quả RunTimedOut, sau hạn + 2 grace và trước 18 giây.
  - Hủy trong action và lúc đóng: Cancelled.
  - Mọi ca đều kiểm `hang.phase`, stack chỉ có `module:hàm:dòng`, driver và Edge của lượt không còn, không còn profile, phiên vẫn CONNECTED.
  - Lượt đúng hạn không bị kết thúc driver.
  - Hai nguồn chạy song song: chỉ driver của nguồn bị treo bị kết thúc; nguồn kia vẫn trả 200, và lượt sau của nguồn bị treo chạy tiếp được.
  - Cửa sổ đăng nhập (bản headless thay cho cửa sổ, fixture, không đăng nhập thật): driver ngừng trả lời khi cửa sổ đang chờ người dùng. Kết quả LOGIN_TIMEOUT hay LOGIN_CANCELLED, không có `failure`, có `hang`, không lưu gì.
- `tests/test_download_account_browser.py`, thêm 1 test: runtime không xác định được driver thì không mở Edge (BrowserUnavailable "DriverUnknown"), runtime vẫn dừng, không còn profile. Các test dùng runtime giả có thêm một driver giả (`FAKE_DRIVER`).
- `tests/test_download_account_runs.py`, thêm 5 test (`HangEscalationTest`, trình duyệt giả):
  - lượt kẹt trong action, đọc trạng thái hay đóng mà kết thúc Edge không gỡ được: Edge sau một grace, driver sau thêm một grace;
  - kết thúc Edge gỡ được thì không bao giờ kết thúc driver;
  - hủy: Cancelled có `hang`;
  - hủy rồi tới hạn chỉ gài một chuỗi;
  - lượt đúng hạn không gài gì.
- `tests/test_download_account_login.py`, thêm 2 test (`StuckDriverWindow`; lời gọi được giải phóng ném `Exception` thường như Playwright thật):
  - hết hạn và hủy: Edge rồi driver, mỗi bước một lần, có `hang`, không chạy lại sau khi lượt xong;
  - cửa sổ được gỡ bằng kết thúc Edge thì không bao giờ kết thúc driver.
  - Test cũ với Edge thật (`test_a_window_stuck_in_a_page_script_is_killed`) vẫn đạt.
- Kiểm ngược: tắt bước kết thúc driver chỉ trong bộ nhớ thì các test mới FAIL.
  - Edge thật: 2 test "action" và "đóng" FAIL vì "the run was still blocked after 60 s", 121 giây; watchdog ngoài gỡ được, không còn tiến trình node hay msedge nào sót lại.
  - Trình duyệt giả: 4 ca runs và 2 ca login FAIL, có giới hạn.
  - Bỏ nhánh bắt lời gọi được giải phóng trong `_open_window` (một bản nạp vào bộ nhớ, file không đổi): 2 ca login giả và 2 ca đăng nhập trên Edge thật FAIL với LOGIN_FAILED (`failure` "Exception") thay cho LOGIN_TIMEOUT/LOGIN_CANCELLED.

**Review chỉ đọc (một agent):** không có CRITICAL hay HIGH.
- Đã sửa:
  - M1: `hang` được ghi sau khi kill nên có thể mất khi lượt kết thúc trước. Nay ghi ngay trước khi kill.
  - M2: cửa sổ đăng nhập có driver bị kết thúc trả LOGIN_FAILED thay cho mã dừng; lệnh cài đặt sau launch nằm ngoài `try`. Kiểm ngược trên Edge thật xác nhận lỗi này.
  - M3: không xác định được driver thì lượt chạy mà không có đường ra. Nay fail closed.
  - M5: test phụ thuộc thời gian khởi động Edge (lượt 5 giây) và thiếu các phase sau launch, runtime dừng và đăng nhập trên Edge thật. Nay lượt 8 giây và có đủ các phase đó.
  - L1: kết thúc Edge thêm một lần sau driver.
  - L3: VERIFIER_ERROR khi đã có lệnh dừng.
  - L6: đọc khung của luồng khi greenlet đang chạy.
  - L7: `poll()` trước khi kill.
  - L8: hai chỗ trong test driver: kill qua handle, đọc stdout có giới hạn.
- Ghi vào giới hạn: M4, L2, L4, L9 (bên dưới).
- L5 không phải lỗi: `Cancelled()` mới ở `download_account_sources` chỉ thay khi lượt đã trả về bình thường (không có treo). Lỗi có `hang` đi thẳng từ `run_with_session` lên.

**Kết quả (code cuối, sau review):**
- Regression độc lập của Codex, chạy tại chỗ: M3 4/4 OK, M2b 2/2 OK.
- Test tập trung (có `BILIFLOW_FFMPEG`; mỗi lệnh có giới hạn ngoài 40 phút): driver, hang, runs, login, browser, sources, listing, transfer, `test_download_sources`, `test_download_dispatch`, `tests.test_cache_dependencies`, `tests.test_stage_cache`. Kết quả: 332 test OK, không skip, 673 giây.
- Riêng `test_download_account_hang`: 8 test OK, 112 giây.
- Lượt trước review: cùng nhóm, 327 test OK, 619 giây. Lượt đó có thêm một lỗi import vì gõ nhầm tên module không tồn tại; không phải test FAIL.
- Không chạy lại toàn bộ suite để chờ lỗi ngẫu nhiên; các ca treo được tái hiện có chủ đích.
- `git diff --check` sạch; file sửa chỉ có host `.example`. Sau test không còn tiến trình node/msedge hay thư mục tạm nào của test.

Giới hạn còn lại của phần này:
- `driver_popen` đọc thuộc tính riêng của Playwright 1.63 và asyncio. Nếu nâng Playwright mà chỗ này đổi thì mọi lượt dừng ngay với BROWSER_UNAVAILABLE ("DriverUnknown"), không bao giờ chạy mà không có đường ra. Test `InstalledPlaywrightTest` sẽ FAIL để báo.
- Watchdog chỉ được gài bởi hạn hoặc lệnh hủy. Lượt đã xong việc mà đóng bị treo thì chờ tới hạn rồi mới kết thúc: lượt ẩn tối đa `run_seconds`, đăng nhập tối đa 600 giây (người dùng thấy LOGIN_BUSY trong lúc đó), cộng 2 grace. Vẫn có giới hạn như đã ghi; gài thêm một watchdog lúc bắt đầu đóng là việc riêng (M4 của review).
- Driver treo ngay trong `start()` (trước khi `start()` trả về) chỉ được ghi muộn từ cùng runtime trong `end_driver`; đường này chưa có test với driver thật.
- Bước kết thúc Edge tìm được trình duyệt chưa gán (`browser` là None, chỉ vài mili giây đầu) thì không gài bước driver (L2). Timer không tạo được (hết luồng) lúc dựng watchdog thì hàm hủy của người gọi không được gỡ (L4).
- Sau khi lượt đăng nhập xong, `LoginRun` còn giữ trình duyệt (và handle của driver đã chết) tới lần đăng nhập sau của nguồn đó (L9).
- `Cancelled` không có thuộc tính lớp `hang` (chỉ `RunTimedOut` có). Log của M4 cần đọc bằng `getattr(error, "hang", None)`.
- Bước hai chỉ giải được lời gọi chờ driver. Một chỗ chặn không phụ thuộc driver (ví dụ code Python bị chặn) thì không giải được; chưa thấy trường hợp này.
- Lượt bị kết thúc driver trước khi đọc được trạng thái thì không lưu cookie xoay. Phiên cũ còn nguyên, không bị xóa hay đổi TTL.
- Vì sao driver không thấy Edge đóng trong lần treo 70 phút vẫn chưa biết. Nếu xảy ra lại, `hang` cho biết giai đoạn và chỗ chờ.
- Test tái hiện treo bằng cách tạm dừng driver, cùng cơ chế pipe không trả lời. Cách này không chứng minh mọi kiểu treo thật.

**Giới hạn còn lại (không phải ba lỗi trên):**
- Máy chủ vừa bỏ qua If-Range vừa không gửi ETag hay Last-Modified trong 206: không có bằng chứng để phát hiện bản khác. Byte được nối theo If-Range như mọi máy chủ.
- Last-Modified được so như chuỗi. Cùng thời điểm mà viết khác định dạng thì bị coi là bản khác và tải lại từ đầu (an toàn, tốn băng thông).
- Link trang trên host portal khác của cùng nguồn (ví dụ `www.`) vẫn không được đọc; danh sách báo chưa đủ. Bộ đọc thật nên trả link cùng host với trang đã dán.
- Đọc trạng thái và đóng trình duyệt quá 5 giây làm vé lấy sát hạn bị bỏ (RunTimedOut, thử lại được); vé đó không được dùng.
- Một trang sau của danh sách treo tới hạn của lượt (mỗi trang được chờ tới 30 giây, phần chừa cho đọc danh sách là 15 giây) làm cả lượt kết thúc bằng RunTimedOut (thử lại được), không trả phần danh sách đã đọc. Hành vi này có từ trước và không đổi ở lượt sửa này. Muốn trả phần đã đọc thì mỗi lần điều hướng cần một hạn riêng, đây là thay đổi của lớp trình duyệt.
- Mọi href bộ đọc khai báo mà không đi theo được (kể cả `javascript:`, `mailto:` hay mục "trang hiện tại" không có link thật) đều làm danh sách chưa đủ. Bộ đọc thật (M7) phải lọc các mục đó; nếu không, mọi danh sách sẽ chưa đủ và phim lẻ một bản sẽ bắt người dùng chọn.
- Bộ đọc trang và bộ xác nhận của nguồn thật vẫn chưa có (`PAGE_READERS`, `LOGIN_VERIFIERS` trống). Sửa xong thư viện M3 không có nghĩa nguồn thật đã tải được: phải khảo sát, làm bộ đọc thật và nghiệm thu với người dùng trước khi nói nguồn đó dùng được.

### 9.14 M4 (2026-10-08): nối hàng đợi/API, chờ đăng nhập, nhóm tải theo tập

Người dùng giao M4 sau khi Codex duyệt bản sửa driver treo (prompt riêng ngoài repo). Phần này ghi thiết kế đã chốt trước khi code; kết quả ở cuối mục.

**Đã chốt trong prompt (không bàn lại):** nhóm dài lưu đủ danh sách và tạo tác vụ con dần dưới mức 100; trần 500 tập mỗi nhóm, vượt thì từ chối rõ; tác vụ trang sau khi tách kết thúc ở `EXPANDED` (nhả slot, không có file riêng, không Thử lại); tập đặc biệt và thứ tự theo M3; giữ all/pick và một bản mỗi tập (bản chung theo `kind` hoặc bản riêng từng tập), không đổi bản hay bỏ tập thiếu bản; điện thoại được xem/chọn tập và điều khiển tải; đăng nhập, hủy đăng nhập, ngắt kết nối chỉ trên PC; không pause toàn cục.

**Trạng thái mới (16 trạng thái):**

| Trạng thái | Slot | Đếm vào 100 | Chặn link | Dừng | Tiếp tục | Hủy | Thử lại | Xóa | Đổi tên |
|---|---|---|---|---|---|---|---|---|---|
| `WAITING_LOGIN` "Chờ đăng nhập" | không | có | có | có (→ STOPPED) | — | có | không | không | có |
| `EXPANDED` "Đã tách thành nhóm tập" | không | không | không (dán lại trang được) | — | — | qua nhóm | không | qua nhóm | không |

- `EXPANDED` thuộc `FINAL_STATES`, `CLOSED_STATES` và `RELEASED_LINK_STATES`; Xóa tác vụ trang = xóa cả nhóm, chỉ khi mọi tập đã kết thúc và không còn tập chờ chỗ. Dọn 30 ngày của `download_upkeep` không xóa tác vụ trang hay nhóm.
- `WAITING_LOGIN` không có luồng chạy; phần đã tải giữ trong `temp\downloads\<id>`; không thuộc `TEMP_CLEANABLE` (không bị dọn 7 ngày).

**Cột mới của `download_tasks`:** `group_id`, `member_id` (unique khi khác NULL), `item_key` (`FileSelection.key`), `account_owner` (SID Windows của tác vụ thuộc nguồn tài khoản), `login_source`, `login_generation`, `login_reason`.

**Bảng mới (cùng `state/downloads.sqlite3`):**
- `download_previews(task_id PK → download_tasks ON DELETE CASCADE, source_id, listing_json, fingerprint, draft_json, revision, created_at, updated_at)`: chỉ `Listing.public()`; không `navigation`, vé, link media hay lease.
- `download_groups(id, parent_task_id UNIQUE, source_id, source_label, account_owner, film, title, url, fingerprint, mode, complete, reasons_json, note, total, width, request_key UNIQUE, request_hash, state ACTIVE|CANCELLED, existing_json, created_at, updated_at)`.
- `download_group_members(id, group_id → download_groups ON DELETE CASCADE, ordinal, item_key, selection_json, season_number, season_label, episode_number, episode_label, special, variant_label, code, status PENDING|HELD|CREATED|CANCELLED, task_id, last_state, intent (thêm ở 9.15); UNIQUE(group_id, ordinal), UNIQUE(group_id, item_key))`.

**Runtime (`download_account_api.AccountRuntime`, trong `DownloadService`):**
- Đọc `config/download_accounts.local.json` của đúng root. Không có nguồn: không tạo manager, không đụng vault. Có nguồn: `AccountManager(root, config)` (vault theo SID tiến trình) + `LoginCoordinator(manager, on_finish=…)`; registry nhận manager này. Lỗi tạo manager: provider từ chối link với ACCOUNT_NOT_READY và lỗi hiện ở trạng thái.
- `start()`: `manager.recover()` và `clear_browser_profiles()` trước khi worker dispatch.
- `stop()` (thứ tự đổi sau review code): dừng worker (không bắt đầu gì mới, lượt ẩn đang chạy kết thúc) → đóng coordinator (từ chối đăng nhập mới, hủy cửa sổ, chờ có giới hạn) → chỉ đóng manager và store khi không còn luồng tải, vòng dispatch hay cửa sổ nào chạy, nếu còn thì một luồng đóng chờ tới lúc rảnh (sửa ở 9.15; trước đó store đóng vô điều kiện); mỗi bước vẫn chạy khi bước trước lỗi.
- Không bao giờ mở cửa sổ đăng nhập lúc khởi động, khi đọc trạng thái, khi thiếu phiên hay khi thử lại. Callback kết thúc đăng nhập chỉ ghi kết quả vào bộ nhớ và đánh thức worker; việc đánh thức tác vụ đọc trạng thái DB.

**Route tài khoản (PC-only):**
- `POST /api/download-accounts/<source_id>/login | cancel-login | disconnect`. Control Center kiểm Host + token như mọi POST, rồi `loopback_client()` (403 `pc_only` ngoài 127.0.0.1). Listener điện thoại từ chối trước khi đọc body nhờ `phone_access.PC_ONLY_PATTERNS`. Đường dẫn không nằm dưới `/api/downloads`, nên không lọt qua các mẫu cho phép của điện thoại.
- Chỉ nhận source id có trong cấu hình (regex id + tra cấu hình); body bị bỏ qua: không nhận URL đăng nhập, cookie, mật khẩu hay module/lệnh.
- Trạng thái nằm trong `GET /api/downloads` (`accounts`): danh sách nguồn (trạng thái M1, có cửa sổ đang mở không, kết quả đăng nhập gần nhất, cảnh báo), lỗi cấu hình, lỗi runtime. Đọc trạng thái không mở trình duyệt.

**WAITING_LOGIN:**
- Chỉ `SourceLoginRequired` (mã SOURCE_LOGIN_REQUIRED) đưa tác vụ vào đây; LOGIN_REQUIRED của máy chủ file (HTTP 401 qua SafeHttp) giữ nguyên đường cũ.
- Ba lối vào: (1) dispatcher kiểm phiên trước khi lấy slot (`login_gate`, chỉ đọc dòng DB: không phiên, hết 3.600 giây, bị nguồn từ chối) → QUEUED → WAITING_LOGIN, không slot, không luồng; (2) PROBING nhận `SourceLoginRequired`; (3) DOWNLOADING nhận nó khi lấy vé mới trước khi tải hay khi làm mới vé giữa chừng. Phần đã tải giữ nguyên.
- Đánh thức: dispatcher (ngay sau callback đăng nhập, và mỗi 30 giây để phủ restart) chuyển WAITING_LOGIN → QUEUED chỉ khi tác vụ cùng SID Windows với manager, đúng nguồn, và phiên hiện dùng được có generation mới hơn generation đã hỏng của tác vụ (hoặc tác vụ chưa từng có phiên). `queued_at`, lựa chọn và nhóm giữ nguyên. Tác vụ đã Dừng hay Hủy không bị đánh thức (không còn ở WAITING_LOGIN); nguồn khác không bị đụng. Tác vụ chờ mà nguồn đã rời cấu hình kết thúc FAILED (ACCOUNT_SOURCE_GONE) ở lần kiểm đó, phần đã tải giữ đến khi Xóa.
- Tác vụ của tài khoản Windows khác (cột `account_owner`) không bao giờ dùng phiên hiện tại: dispatcher chuyển nó sang WAITING_LOGIN với lý do OTHER_ACCOUNT; chỉ manager của đúng SID đánh thức. Tác vụ cũ chưa có chủ được gán SID hiện tại ở lần dispatch đầu.

**Lỗi và truyền file:**
- File đang tải không bị cắt vì hết TTL hay ngắt kết nối: SafeHttp không dùng phiên; chỉ lần xin vé kế tiếp mới kiểm phiên.
- Vé hết hạn: làm mới có giới hạn như M3. Một 403 không phải đăng nhập. Mạng, DNS, timeout driver, ổ đĩa, file bị xóa, nguồn đổi giữ mã riêng.
- `getattr(error, "hang", None)`: sự kiện SOURCE_RUN_HUNG ghi giai đoạn và các khung module:function:line, không ghi exception thô.
- `profile_left` và lưu cookie xoay thất bại: provider báo qua `ResolveContext.notice` mới (tùy chọn, có test). Worker ghi sự kiện cảnh báo của tác vụ; provider giữ cảnh báo gần nhất cho trạng thái nguồn. Không xóa phiên, không kéo dài TTL, không coi là hết đăng nhập.

**Dán trang phim, preview:**
- Host portal của nguồn đã cấu hình → AccountSourceProvider; không bao giờ sang yt-dlp khi thiếu phiên, thiếu bộ đọc hay lỗi xác thực.
- Nguồn bị loại vì cấu hình sai: `AccountConfig.dropped` giữ host (đọc được) và lý do. Link thuộc host đó bị từ chối ngay lúc dán (ACCOUNT_SOURCE_INVALID, kèm lý do), trừ host mà provider công khai giữ (xung đột với `download_providers.local.json`): link đó đi đường ẩn danh như cũ, có sự kiện cảnh báo ACCOUNT_SOURCE_IGNORED. Không dùng phiên ở đường ẩn danh.
- `SourceNeedsEpisodes` → NEEDS_CHOICE kiểu `episodes` (`probe.choice_kind`), nhả slot. Preview lưu trong `download_previews` để refresh/restart không mất; snapshot chỉ có tóm tắt (tên, số tập, đầy đủ hay không, fingerprint, nháp), đọc từ cột `summary_json` ghi lúc lưu, không đọc lại danh sách. Trang rời NEEDS_CHOICE (Hủy, tách nhóm) thì danh sách được xóa ở lượt dispatch kế tiếp.

**API chọn tập (cho phép trên điện thoại):**
- `GET /api/downloads/<id>/episodes`: preview công khai, fingerprint, nháp (`draft`, `revision`) và kế hoạch của nháp.
- `POST /api/downloads/<id>/episodes/draft {selection, fingerprint, revision}`: lưu nháp; `revision` cũ → 409 STALE_DRAFT.
- `POST /api/downloads/<id>/episodes/confirm {selection, fingerprint, idempotency_key, confirm_scope, skip_existing}`.
- `selection = {mode: "all"|"pick", episodes: [...], variant_kind: "…"}` hoặc `{…, variants: {episode: variant_id}}`. Server kiểm quyền và trạng thái, mọi id thuộc preview đã lưu, mỗi tập đúng một bản. Lỗi: STALE_PREVIEW, BAD_SELECTION, VARIANT_MISSING, VARIANT_AMBIGUOUS, SCOPE_NOT_CONFIRMED (danh sách chưa đủ mà chưa xác nhận "N tập đã thấy"), GROUP_TOO_LARGE (> 500), ITEMS_EXIST (tập đã có trong lượt chưa đóng hay trong nhóm khác đang chờ; trả danh sách; `skip_existing: true` tạo nhóm không có các tập đó và ghi chúng vào `existing`), NOT_WAITING, IDEMPOTENCY_CONFLICT. Không lộ dữ liệu riêng.

**Nhóm, idempotency, backpressure:**
- Một transaction: kiểm `request_key` → kiểm tác vụ trang còn NEEDS_CHOICE kiểu episodes và fingerprint → kiểm trùng → tạo nhóm và toàn bộ thành viên theo thứ tự (PENDING) → CAS tác vụ trang NEEDS_CHOICE → EXPANDED → xóa preview. Lỗi ở bất kỳ bước nào thì rollback, tác vụ trang không đổi.
- Lặp yêu cầu cùng `idempotency_key` (cùng nội dung) trả lại nhóm cũ; khác nội dung → 409. Hai xác nhận đồng thời: một thắng; bên kia gặp tác vụ trang đã EXPANDED (409, kèm id nhóm). `parent_task_id UNIQUE`, `UNIQUE(group_id, item_key)` và index unique `member_id` chặn trùng ở mức DB.
- Khóa thành viên: nguồn + phim + tập + bản (`FileSelection.key`), không chỉ URL trang. Trùng = một dòng chưa nhả link (không phải CANCELLED/EXPIRED) cùng `item_key`, hoặc thành viên PENDING/HELD của nhóm khác (như quy tắc trùng URL hiện có).
- Tạo tác vụ con (`fill`): mỗi lượt dispatch, trong một transaction dưới khóa store: chỗ trống = 100 − số dòng chưa đóng; lấy thành viên PENDING theo (nhóm, thứ tự); mỗi thành viên CAS PENDING → CREATED cùng lúc thêm tác vụ QUEUED (URL trang, `probe.account_file`, `item_key`, `group_id`, `member_id`, `account_owner`). Phần còn lại chờ chỗ: không slot, không vé, không mất khi restart.
- Hủy nhóm: trước tiên chuyển mọi thành viên PENDING/HELD → CANCELLED và nhóm → CANCELLED (fill bỏ qua), rồi Hủy từng tập chưa xong, gồm cả tập đã dừng, bị ngắt hay lỗi; ý định Dừng/Tiếp tục/Hủy được lưu và áp dụng lại sau khi bị ngắt (9.15). Dừng nhóm: PENDING → HELD, Dừng từng tập. Tiếp tục nhóm: HELD → PENDING, Tiếp tục từng tập đã dừng/bị ngắt. Thử lại nhóm: Thử lại từng tập lỗi hoặc đã dọn file tạm (FAILED/EXPIRED; tập dừng hay bị ngắt dùng Tiếp tục, tập đã xong không bao giờ tải lại); tập EXPIRED chỉ được thử lại khi danh sách còn chỗ dưới 100; nhóm đã hủy thì từ chối (409 GROUP_CANCELLED). Xóa nhóm: khi mọi tập đã kết thúc và không còn PENDING/HELD.
- Không nâng mức 100. Nhóm dài có thể chiếm hết chỗ; link thường dán lúc đó bị từ chối TOO_MANY_TASKS như hiện có.

**Vé, slot, khóa:** không xin vé khi xem/lưu preview hay xác nhận nhóm; mỗi tập xin vé ở PROBING/DOWNLOADING với slot của nó (M3). Khóa trình duyệt theo root + SID + nguồn của M3; không giữ khóa worker/store/manager khi Playwright chạy. Giữ 2 slot mặc định, tối đa 3.

**Thứ tự và tên file:** thứ tự cố định theo M3; tên `NNN - <phim> - <mã tập>` với độ rộng `max(3, số chữ số của tổng)`. Mã tập: `S01E05` (có số mùa và số tập), `E05` (không có số mùa), `SP03`/`S01SP03` (đặc biệt có số), nhãn gốc của tập (không có số; đặc biệt thêm `SP `). Khi vượt 150 ký tự chỉ cắt phần tên phim (chừa chỗ cho " (N)" khi trùng). Đổi tên một tập chỉ đổi phần tên phim. Không ghi đè; giữ MKV.

**Điều khiển từng tập:** như hiện có. Thử lại một tập giữ thứ tự và tên trong nhóm, khôi phục `probe.account_file` từ thành viên (không mất lựa chọn), đưa về cuối hàng; bị từ chối (409) khi cùng file tập đó đang có ở một lượt chưa đóng khác hay đang chờ trong nhóm khác, và khi nhóm của tập đã hủy (cả Tiếp tục, 9.15). Xóa một tập ghi `last_state` vào thành viên.

**Xác nhận dùng danh sách đã lưu (khác ghi chú ở 9.12):** "Tải N tập" không chạy lượt ẩn nào: server so `fingerprint` của yêu cầu với danh sách đã lưu (cái người dùng đã thấy) và lập kế hoạch từ đó. Danh sách trên nguồn đổi sau đó thì mỗi tập phát hiện ở lượt PROBING của nó (đọc danh sách tới đúng file, so mã phim/tập/bản; khác thì SOURCE_CHANGED), không chọn bản khác thay. Làm vậy để xác nhận không cần phiên, không xin vé và không giữ khóa nào khi Playwright chạy.

**Kết quả M4 (2026-10-08).** Code và test trong worktree, chưa commit. Chưa giao diện (M5).

File mới: `download_account_api.py` (runtime + route PC), `download_account_tasks.py` (mixin hàng đợi: chờ đăng nhập, chọn tập, nhóm), `download_groups.py` (preview, nhóm, fill, tóm tắt), `download_episode_names.py` (tên tập). Sửa: `download_store.py` (2 trạng thái, 7 cột, 3 bảng), `download_worker.py`, `download_source_steps.py`, `download_api.py`, `download_sources.py` (`dropped_account`), `download_accounts.py` (`session_gate`), `download_account_login.py` (`on_finish`, `active`), `download_account_sources.py` (`login_gate`, `usable_generation`, `account_sid`, cảnh báo), `download_account_config.py` (`DroppedSource`), `download_source_types.py` (`ResolveContext.notice`), `phone_access.py`, `control_center.py`; JS: `download-core.js` (16 trạng thái, nhãn, tông, nhóm lọc, tập nút) và `verify-download.cjs`; test: `test_dashboard_v2_review.py` (mẫu POST điện thoại).

Test mới (fixture `tests/account_queue_fixtures.py`: danh sách giả qua `ListingBuilder` thật, provider có `login_gate`/`usable_generation` thật, `AccountManager` thật với vault giả và đồng hồ giả, file tự làm qua FixtureServer 127.0.0.1):

| Bộ | Số test | Nội dung chính |
|---|---|---|
| `test_download_groups.py` | 24 | Tên tập (tiền tố ≥ 3 chữ số, mã tập theo số của nguồn, chỉ cắt tên phim, tên trùng không ghi đè, MKV); danh sách lưu dựng lại đúng fingerprint, danh sách bị sửa bị từ chối; kiểm lựa chọn chặt (mode, mã giả, bản giả); nháp + revision qua restart, STALE_DRAFT/STALE_PREVIEW; nhóm 12 tập theo thứ tự và trang EXPANDED; hai mùa + tập đặc biệt; thiếu bản/hai file cùng bản (không ghi gì), bản riêng từng tập; danh sách chưa đủ cần xác nhận "N tập đã thấy"; trần 500; lặp yêu cầu, khóa dùng lại, 8 xác nhận đồng thời → một nhóm; tập đã có (ITEMS_EXIST, `skip_existing`); fill dưới 100 (nhóm 25 với 90 lượt có sẵn), WAITING_LOGIN và NEEDS_CHOICE đếm vào 100, restart không tạo trùng, Dừng/Tiếp tục/Hủy thành viên chưa có tác vụ, phần trăm chỉ khi đủ dung lượng, snapshot chỉ đếm `existing` |
| `test_download_account_queue.py` | 25 | Worker thật + provider giả có cổng phiên thật: chờ đăng nhập không slot, không vòng lặp, link khác vẫn chạy, không lượt ẩn nào; đánh thức đúng nguồn + tài khoản Windows, giữ `queued_at`; TTL 3.600 giây với đồng hồ giả, callback cũ không đánh thức, lần kiểm định kỳ đánh thức sau restart; Dừng/Hủy khi chờ không bị đánh thức; nguồn rời cấu hình khi đang chờ → FAILED; phiên hết giữa lúc tải giữ phần đã tải rồi tải nối sau đăng nhập; TTL không cắt file đang tải; một 403 không phải đăng nhập; vé hết hạn, lỗi mạng giữ mã riêng; treo driver ghi giai đoạn và khung, không ghi nội dung lỗi; cảnh báo lưu cookie/hồ sơ tạm không đụng phiên; nguồn bị bỏ khỏi cấu hình: link cũ FAILED (không sang yt-dlp), link mới bị từ chối khi dán trừ host của provider công khai; trang phim nhiều tập chờ chọn, danh sách và nháp qua restart, không vé/file trước khi xác nhận; tên `NNN - <phim> - <mã>` dù xong ngược thứ tự, Thử lại nhóm không tải lại tập đã xong; Thử lại giữ chỗ, đổi tên chỉ đổi phần tên phim; hành động nhóm xử lý thành viên chờ trước; không tải trùng tập khi Thử lại; trang hủy bỏ danh sách lưu; trang mất nhóm vẫn xóa được; Thử lại một lượt giữ mức 100; MKV và tên trùng; route PC/điện thoại qua handler Control Center (preview/nháp/xác nhận, 4 xác nhận đồng thời, route tài khoản PC-only bỏ qua body mồi, điện thoại bị chặn đăng nhập nhưng được chọn tập và điều khiển nhóm) |
| `test_download_account_api.py` | 12 | Không có cấu hình: không manager, không tạo file nào; đăng nhập PC bằng cửa sổ giả, kết quả đánh thức hàng đợi, không lộ cookie; đăng nhập thứ hai bận, hủy; ngắt kết nối đóng cửa sổ đang mở; nguồn lạ 404, chưa có bộ xác nhận 409 trước khi mở cửa sổ; khởi động dọn lượt đăng nhập bị ngắt, không mở cửa sổ; tắt máy từ chối đăng nhập mới; thứ tự dừng worker → tài khoản → đóng manager, manager giữ mở khi cửa sổ còn chạy, worker lỗi vẫn đóng tài khoản và store; lỗi DB/ổ đĩa trả thông báo cố định; snapshot có trạng thái tài khoản, không có bí mật |

Chạy lại: toàn bộ suite (`unittest discover`, chạy trước các bản sửa sau review): 2.350 test, 28 ERROR, 26 skip; cả 28 ERROR ở `test_job_ocr_option` và `test_job_pipeline` vì worktree chưa có file `.mp4` trong `input\` (mục M6 đã ghi điều kiện này); sau khi đặt một clip tổng hợp 1 giây (FFmpeg testsrc) vào `input\` của worktree, hai module đó 35/35 OK. Sau các bản sửa: toàn bộ `test_download*` 862 OK, không skip (844 giây; có các test treo driver với watchdog ngoài); dashboard tải/điện thoại/hardening/hợp đồng/review + `test_stage_cache` 84 OK (1 skip); `test_cache_dependencies` 13 OK, khóa cache của 10 stage quét không chứa file `download_*`, `control_center.py` hay `phone_access.py`; Node gate `verify-adapter` 37/0, `verify-download` 22/0, `verify-review` 32/0, `verify` 38/0; regression của Codex M3 4/4 và M2b 2/2. Không còn tiến trình hay thư mục tạm của test.

Review: hai agent chỉ đọc (code, bảo mật), không có CRITICAL hay HIGH. Đã sửa: snapshot đọc tóm tắt preview từ cột `summary_json` và đếm thành viên nhóm bằng SQL, `existing` chỉ ở chi tiết nhóm (snapshot có `existing_count`); lỗi cơ sở dữ liệu/ổ đĩa của route tải trả thông báo cố định; nhóm và trang EXPANDED xóa trong một transaction, trang mất nhóm vẫn xóa được; danh sách lưu bị xóa khi trang rời NEEDS_CHOICE; Thử lại một lượt đã đóng giữ mức 100; `stop()` dừng worker trước, mỗi bước có `finally`; tác vụ chờ đăng nhập của nguồn đã rời cấu hình kết thúc FAILED. Ghi vào giới hạn: khóa worker khi Hủy/Xóa nhóm lớn, `fill` một transaction, phạm vi của điện thoại/Tailscale không mã, escape ở M5. Giữ nguyên: lỗi cấu hình tài khoản chỉ hiện scheme + host của giá trị (`_shown`, có từ M1).

**Hợp đồng API cho M5** (mọi phản hồi là JSON; lỗi có `error` tiếng Việt và `code`; id là số nguyên dương ≤ 12 chữ số):

- `GET /api/downloads` thêm:
  - mỗi tác vụ: `group_id`, `login_source`, `login_reason` (NOT_CONNECTED, SESSION_EXPIRED, SESSION_REJECTED, OTHER_ACCOUNT…), `choice_kind` (`"episodes"` khi trang phim nhiều tập chờ chọn, ngược lại null), `episodes` (chỉ khi NEEDS_CHOICE kiểu episodes: `title, kind, episode_count, complete, message, fingerprint, revision, has_draft`), `group` (tác vụ tập: `group_id, ordinal, total, code, planned_name`);
  - `groups`: tóm tắt từng nhóm `id, parent_task_id, source_id, source_label, title, state (ACTIVE|CANCELLED), mode, complete, note, reasons[], total, done, counts{pending, held, queued, waiting_login, running, completed, failed, stopped, interrupted, cancelled, expired, removed, attention}, percent (null khi có tập chưa biết dung lượng), finished, existing_count, existing (null trong snapshot; danh sách ở chi tiết nhóm và phản hồi xác nhận), created_at`;
  - `accounts`: `{sources: [{id, label, state, session_check, authenticated_at, recheck_at, checked_at, error_code, message, login_supported, reader_supported, login_running, last_login {code, message, connected, profile_left, failure, hang, at} | null, warnings [{code, message, at}], waiting_tasks}], problems, problem_text, error}`.
- `GET /api/downloads/<id>/episodes` → `{task_id, listing (Listing.public()), fingerprint, draft, revision, plan, plan_error, max_episodes}`; `plan = {mode, count, complete, confirm_label ("Tải N tập" | "Tải N tập đã thấy"), note, max, too_large, missing[{episode, label}], ambiguous[…]}`. 409 NOT_WAITING `{group_id, state}` khi trang đã tách nhóm.
- `POST /api/downloads/<id>/episodes/draft {selection, fingerprint, revision}` → `{task_id, revision, draft, plan, plan_error}`. 400 BAD_SELECTION `{unknown[]}`; 409 STALE_PREVIEW, STALE_DRAFT `{revision}`, NOT_WAITING.
- `POST /api/downloads/<id>/episodes/confirm {selection, fingerprint, idempotency_key (8–64 ký tự [A-Za-z0-9_-]), confirm_scope?, skip_existing?}` → `{group, replay, existing[]}`. Lỗi: 400 BAD_SELECTION, BAD_REQUEST_KEY, VARIANT_MISSING / VARIANT_AMBIGUOUS `{episodes[{episode, label}], episode_count}`, SCOPE_NOT_CONFIRMED `{count, confirm_label}`, GROUP_TOO_LARGE `{count}`; 409 STALE_PREVIEW, NOT_WAITING `{group_id, state}`, ITEMS_EXIST `{existing[{episode, label, task_id | group_id, state}], existing_count}`, IDEMPOTENCY_CONFLICT, BAD_PREVIEW. Gửi lại cùng khóa và cùng nội dung trả lại nhóm cũ (`replay: true`), cả sau khi trang đã EXPANDED.
- `selection = {mode: "all" | "pick", episodes: [mã tập] (chỉ với pick), variant_kind: "<kind>"}` hoặc `{…, variants: {mã tập: mã bản}}` (không cả hai; không có gì khi mỗi tập chỉ có một file). `kind` lấy từ `listing.variant_kinds[].kind`.
- `GET /api/downloads/groups/<id>` → `{group, members: [{id, group_id, ordinal, season_number, season_label, episode_number, episode_label, special, variant_label, code, status (PENDING|HELD|CREATED|CANCELLED), task_id, last_state, task_state, downloaded_bytes, total_bytes, estimated_bytes, output_size, desired_name, error_code, error_message, login_source, login_reason}]}`.
- `POST /api/downloads/groups/<id>/stop | resume | cancel | retry` → `{group}`; `…/remove` → `{group_id, removed, freed_bytes}` (400 khi còn tập chưa kết thúc hay còn PENDING/HELD: Hủy nhóm trước). 409 GROUP_CANCELLED cho resume/retry của nhóm đã hủy; 404 NOT_FOUND. `POST /api/downloads/<id>/resume | retry` của một tập thuộc nhóm đã hủy: 409 `{error}` (9.15).
- Tác vụ trang EXPANDED: không Dừng/Hủy/Thử lại/Đổi tên; `POST /api/downloads/<id>/remove` = xóa cả nhóm (cùng điều kiện).
- PC-only (127.0.0.1 + token + Host; điện thoại 403): `POST /api/download-accounts/<source_id>/login` → 202 `{source, login: "STARTED"}`; `/cancel-login` → `{cancelled, source}`; `/disconnect` → `{source}`. Lỗi: 404 ACCOUNT_UNKNOWN, 409 LOGIN_UNSUPPORTED / LOGIN_BUSY / ACCOUNT_BUSY, 503 ACCOUNT_NOT_READY / SHUTTING_DOWN / ACCOUNT_STATE_ERROR, 403 `pc_only`. Body bị bỏ qua. Kết quả đăng nhập đọc lại qua `accounts.sources[].last_login` của `GET /api/downloads`.
- Lỗi cơ sở dữ liệu hay ổ đĩa của mọi route tải: 503 DOWNLOAD_STATE_ERROR / 500 DOWNLOAD_DISK_ERROR với `kind` (tên loại lỗi), không kèm nội dung lỗi.
- Điện thoại: được `GET` các route trên và `POST` draft/confirm/nhóm như các thao tác tải khác; giao diện M5 chỉ hướng dẫn "Đăng nhập trên PC".

**Giới hạn và việc còn lại:**
- Chưa giao diện: M5 làm hộp chọn tập, tiến độ nhóm, panel tài khoản. `download-core.js` chỉ có nhãn, tông, nhóm lọc và tập nút của 2 trạng thái mới; hộp chọn cũ chỉ vẽ khi có `entries`, nên trang kiểu episodes không vỡ.
- Chưa nguồn thật nào có bộ đọc trang hay bộ xác nhận đăng nhập (`LOGIN_VERIFIERS` rỗng): Đăng nhập của nguồn thật trả 409 LOGIN_UNSUPPORTED, link của nó dừng ở READER_UNSUPPORTED. M4 không chứng minh nguồn thật nào tải được (M7).
- Mỗi tập vẫn chạy hai lượt ẩn (PROBING và lấy vé mới khi tải) như M3; nhóm dài tốn thời gian mở trình duyệt. Tối ưu (dùng lại vé của PROBING) để sau khi có số đo thật.
- Nhóm dài có thể chiếm hết 100 chỗ; link thường dán lúc đó bị TOO_MANY_TASKS. Thử lại một lượt đã đóng (Đã hủy, Đã dọn file tạm) giờ cũng bị từ chối khi danh sách đủ 100 (trước M4 thì không).
- Hủy hay Xóa một nhóm lớn chạy thao tác từng tập (xóa thư mục tạm) trong khóa của worker; nhóm 500 tập có phần đã tải lớn có thể giữ khóa vài giây (không deadlock, không có trình duyệt).
- `fill` tạo tác vụ của mọi nhóm trong một transaction; một thành viên không chèn được (hiện không xảy ra) sẽ chặn cả lượt, lỗi ghi vào `worker_error` và lượt sau thử lại.
- Điện thoại và thiết bị Tailscale cùng tài khoản (không cần mã) xếp được nhóm tập bằng phiên đã đăng nhập, như dán link hiện nay; đăng nhập/ngắt kết nối chỉ trên PC. Rủi ro này chưa được người dùng xác nhận riêng (ghi chú của review bảo mật).
- M5 phải escape mọi chuỗi đến từ trang nguồn (nhãn tập/bản, tên phim, thông báo) khi dựng HTML.
- Đánh thức theo lần kiểm 30 giây và callback trong bộ nhớ; kết quả đăng nhập gần nhất (`last_login`) mất khi khởi động lại (trạng thái phiên thì không).
- Tác vụ của tài khoản Windows khác chỉ chờ (OTHER_ACCOUNT); Hủy hay Dừng vẫn được.

### 9.15 Sửa sau review M4 của Codex (2026-10-08)

Codex review M4 và thấy ba lỗi P2, tái hiện bằng 3 test độc lập (3/3 FAIL trước khi sửa). Phần này ghi nguyên nhân, cách sửa và hợp đồng mới; các mục của 9.14 trái với phần này được thay bằng phần này.

**1. Hủy nhóm bỏ sót tập đã dừng, bị ngắt hay lỗi.**
- Nguyên nhân: `_group_task_action` chỉ hủy tác vụ không thuộc `FINAL_STATES`. Đó là tập trạng thái được phép Xóa, không phải "đã tải xong", nên tập STOPPED, INTERRUPTED, FAILED giữ nguyên, vẫn tính vào mức 100 và giữ khóa trùng `item_key`.
- Sửa: dùng đúng tập `CANCELLABLE` của Hủy từng tập (trừ CANCELLING). Hủy nhóm hủy QUEUED, WAITING_LOGIN, NEEDS_CHOICE, các trạng thái đang chạy, STOPPED, INTERRUPTED và FAILED.
  - COMPLETED giữ file trong `input\`.
  - PUBLISHING chạy nốt; nếu chuyển file thất bại (INTERRUPTED hoặc FAILED) thì lượt dispatch sau hủy nó.
  - CANCELLING giữ đường dọn có thử lại sẵn có (`_finish_cancel`, `_reconcile`); không báo CANCELLED khi file tạm còn bị giữ, kể cả qua restart. Xóa nhóm chờ tới khi dọn xong.
  - Tập đã hủy nhả chỗ trong mức 100 và khóa trùng tập.

**2. Nhóm đã hủy vẫn chạy tập sau khi bị ngắt và khởi động lại.**
- Nguyên nhân: Hủy nhóm ghi nhóm CANCELLED rồi mới hủy từng tác vụ. Ngắt giữa hai bước để lại tác vụ QUEUED hay WAITING_LOGIN mà recovery và dispatcher không biết là đã bị hủy.
- Sửa: ý định của thao tác nhóm được lưu trước khi đụng tác vụ nào, cùng transaction với thay đổi thành viên:
  - Hủy nhóm: trạng thái CANCELLED của nhóm là ý định bền cho mọi tập của nhóm, kể cả tập đã có tác vụ (thành viên PENDING/HELD → CANCELLED như cũ).
  - Dừng / Tiếp tục nhóm: cột mới `download_group_members.intent` (`STOP`/`RESUME`), ghi cho thành viên có tác vụ đang ở trạng thái áp dụng được (STOPPABLE/RESUMABLE). Index một phần `WHERE intent IS NOT NULL`. DB cũ được thêm cột khi mở.
  - `_settle_groups()` áp dụng phần còn lại. Nó chạy trong mỗi lượt dispatch ngay sau `_reconcile()`, trước `_account_upkeep()` (tạo tác vụ từ thành viên và đánh thức đăng nhập), và ở cuối `recover()`. Lặp lại an toàn:
    - chỉ hủy tác vụ còn hủy được;
    - CANCELLING không có luồng thì để `_reconcile` dọn lại;
    - CANCELLING còn luồng mà luồng chưa nhận yêu cầu hủy thì gửi một lần;
    - ý định Dừng/Tiếp tục áp dụng một lần rồi xóa; tác vụ đã đổi trạng thái thì chỉ xóa ý định.
  - Hai chốt thêm:
    - dispatcher không nhận tác vụ QUEUED của nhóm đã hủy (khi việc hủy nó lỗi ở lượt đó);
    - `_resume_waiting_logins` không đánh thức tác vụ của nhóm đã hủy.

    Không mở trình duyệt, không xin vé cho tập của nhóm đã hủy hay đang hủy. Callback đăng nhập đến muộn chỉ đặt cờ; lượt dispatch kế tiếp settle trước khi đánh thức.
  - Tiếp tục / Thử lại **một tập** của nhóm đã hủy bị từ chối: 409 "Tập này thuộc nhóm tập #N đã hủy nên không tải tiếp được; dán lại trang phim rồi chọn tập để tải lại." Nhóm không bao giờ mở lại, nên không có vòng QUEUED → CANCELLED. Tiếp tục/Thử lại cả nhóm vẫn 409 GROUP_CANCELLED. Hủy lại nhóm đã hủy chỉ chạy lại bước settle.
  - Thao tác riêng của một tập (Dừng, Hủy, Tiếp tục, Thử lại) thay ý định nhóm còn treo của tập đó. Ý định chỉ bị xóa sau khi trạng thái đã đổi, nên crash giữa hai bước chỉ làm lượt settle sau thấy việc đã xong.
  - Recovery không xếp lại tác vụ đang chạy lúc crash: ý định Tiếp tục chỉ ghi cho tập đang Dừng/Bị ngắt lúc người dùng bấm.
  - Không đổi: tác vụ ngoài nhóm, và tập bị hủy riêng trong nhóm còn ACTIVE. Thử lại tập đó vẫn được, với chốt trùng tập và mức 100.

**3. Shutdown đóng SQLite khi luồng còn dùng.**
- Nguyên nhân: `DownloadService.stop` gọi `store.close()` vô điều kiện sau `worker.shutdown()`. Hàm này có hạn chờ và có thể trả về khi tác vụ chưa thoát.
- Sửa:
  - `stop()` vẫn có giới hạn (worker 20 giây, cửa sổ đăng nhập 30 giây) và từ chối lượt mới (`_stopping` của worker, coordinator đóng).
  - Manager và store chỉ đóng khi `worker.finished()` (không còn luồng tác vụ hay vòng dispatch, kể cả lượt sweep sau một pass) và không còn cửa sổ đăng nhập (`accounts.busy()`).
  - Nếu còn: một luồng đóng duy nhất (daemon) kiểm lại mỗi 0,5 giây và đóng ngay khi rảnh (`close_when_idle`). Nó idempotent, chỉ đóng đối tượng của chính service đó, và không đụng service mới mở lại cùng root.
  - `stop()` lặp hay đồng thời chờ lần đầu (`_stop_lock`) và không đóng lần hai.
  - `worker.shutdown()` lỗi: vẫn dừng coordinator, vẫn chỉ đóng khi rảnh, rồi ném lại lỗi để Control Center ghi DOWNLOADS_STOP_FAILED như cũ.
  - Sau khi đóng, route tải trả 503 DOWNLOAD_STATE_ERROR; dispatch trả rỗng.
  - Không giữ khóa worker hay store khi chờ; `_close_guard` chỉ bao bước đóng.

**Thay đổi hợp đồng cho M5:**
- `POST /api/downloads/<id>/resume | retry` của tập thuộc nhóm đã hủy → 409 `{error}`.
- Hủy nhóm: mọi tập chưa xong → "Đã hủy". Tập "Đang hủy" (file tạm bị giữ) ở đó tới khi dọn được.
- Bảng `download_group_members` thêm `intent`.

**Kết quả:** Codex M4 3/3 (trước khi sửa 3/3 FAIL), M3 4/4, M2b 2/2; toàn bộ suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree, watchdog ngoài 60 phút): 2.380 test trong 1.187 giây, 2.352 OK, 26 skip, 2 ERROR. Cả 2 ERROR ở `test_download_account_hang` (Edge headless thật, có từ M3, không đổi trong lượt này): lượt không treo phải xong trong hạn 8 giây mà Edge khởi động chậm khi cả suite cùng chạy; chạy riêng module đó 8/8 OK (113 giây). 26 skip: 11 thiếu FFmpeg của project trong worktree, 5 thiếu preview ident Golden, 4 thiếu bộ nhớ logo studio của project, 1 thùng rác thật (opt-in), 1 không có DB Control Center thử, 1 nhãn v1 không ở revision 469, 1 thiếu bộ phân loại an toàn đã cài, 1 thiếu Playwright cho kiểm trình duyệt điện thoại, 1 thiếu dữ liệu phim thật; không skip nào thuộc test tải. Chi tiết ở mục 10.

**Giới hạn:**
- Luồng đóng chờ không hạn nếu một luồng tác vụ không bao giờ kết thúc (ví dụ tiến trình con không chết được). Khi đó store mở tới lúc tiến trình thoát, không bị đóng ép.
- `_settle_groups` chạy trong khóa worker mỗi lượt dispatch (vài truy vấn có index). Hủy nhóm lớn vẫn xóa thư mục tạm trong khóa worker như 9.14.
- Thử lại nhóm không lưu ý định (một lần bấm); bị ngắt giữa chừng thì bấm lại.
- Test có sẵn, không đổi trong lượt này, có thể lỗi khi máy nặng:
  - `test_download_account_hang`: lượt Edge thật không treo phải xong trong 8 giây;
  - `test_download_worker.SpaceTests.test_waits_for_space_then_downloads`: đọc thông báo ngay khi trạng thái đổi.

  Chạy riêng đều đạt. Cả hai đã sửa ở mục 9.16 (chỉ sửa test).

### 9.16 Giai đoạn A trước M5: ổn định test (2026-10-08)

Codex xác nhận ba lỗi P2 của M4 đã sửa (mục 9.15) nhưng chưa có full suite sạch: còn hai ERROR ở `test_download_account_hang`. Prompt mới (ngoài repo) yêu cầu giải thích và xử lý hai ERROR, kiểm race của `SpaceTests`, chạy full suite trên code cuối đạt 0 FAILURE/ERROR rồi mới làm M5. Lượt này chỉ sửa test; không đổi code production, timeout production hay scheduler.

**1. Hai ERROR `KeyError: 'value'` ở `test_download_account_hang`.**
- Test: `HungDriverTest.test_a_run_that_ends_in_time_never_has_its_driver_ended` và `HungDriverTest.test_two_sources_at_once_only_the_hung_runs_driver_is_ended_and_both_go_on` (lượt `again`). `result["value"]` không có vì lượt bình thường kết thúc bằng `RunTimedOut('Trang nguồn chưa xong trong 8 giây…')`.
- Bằng chứng: script trong scratchpad của phiên, Edge headless thật trên fixture. Tải CPU nhân tạo là tiến trình đốt CPU do chính script tạo rồi dừng. Máy có 12 CPU logic.
  - Máy rảnh: lượt bình thường 3,6 giây (khởi chạy Edge 0,5 giây, nạp phiên `set_storage_state` 2,2 giây, đóng trình duyệt 0,3 giây).
  - 12 tiến trình đốt CPU: khởi chạy Edge vẫn khoảng 1 giây, nhưng nạp phiên mất 8–10 giây và đóng context Edge 18–38 giây.
  - Lượt bình thường lúc đó 20–24 giây, nên kết thúc bằng `RunTimedOut` 8 giây với `hang` None, gây `KeyError: 'value'` như trong full suite.
- Kết luận: lỗi của test, không phải production.
  - Test giả định cả lượt bình thường (khởi động, nạp phiên, điều hướng, đóng) xong trong deadline 8 giây vốn dùng để cố ý làm treo. Production cho mỗi lượt ẩn 180 giây (`HIDDEN_RUN_SECONDS`), grace 10 giây.
  - Các test cố ý treo mang cùng giả định: điểm treo nằm sau phần khởi động thật.
- Sửa (chỉ `tests/test_download_account_hang.py`):
  - Lượt phải tự xong có hạn `NORMAL_RUN = 45` giây, dưới watchdog ngoài 60 giây. Test kiểm thêm:
    - không ai yêu cầu dừng lượt;
    - chỉ timer deadline từng được hẹn, và nó đã bị hủy khi lượt đóng (không kill muộn).

    Các kiểm cũ giữ nguyên: chờ 2 × grace + 0,5 giây, `hang` None, driver đã tự kết thúc.
  - Lượt cố ý treo: deadline ngắn 8 giây tính từ lúc driver ngừng trả lời.
    - Bước dừng là của chính lượt đó: cùng `_RunStop._stop("timeout")` mà timer deadline gọi, hẹn qua `_later` của nó nên `close` hủy được.
    - Với cửa sổ đăng nhập: `_stop_run(…, "timeout")` và `_kill_if_hung`, đúng như `_sign_in` hẹn.
    - Deadline tính từ đầu lượt (45 giây) chỉ là lưới đỡ.
    - Giữ một trường hợp đúng thời gian production: driver treo trước khi Edge khởi động, deadline 8 giây tính từ đầu lượt.
  - Giữ nguyên mọi kiểm tra cũ:
    - RunTimedOut/Cancelled, đúng pha, khung module:function:line;
    - driver và Edge của đúng lượt đã kết thúc, hồ sơ đã dọn, phiên giữ nguyên;
    - nguồn khác vẫn sống và driver của nó không bị kết thúc; khóa nguồn được nhả;
    - cận `BOUND = RUN + 2 × GRACE + 8` (nay tính từ lúc treo), "không kill muộn".
  - Thêm cho test hai nguồn: pha `page` và cận thời gian của alpha.
  - Bản sửa đầu có lỗi, đã tìm ra khi lấy bằng chứng:
    - Khi hai lượt lồng nhau, lớp ghi `_RunStop` của lượt sau kế thừa lớp đã patch của lượt trước. `_RunStop` của alpha bị ghi hai lần nên deadline từ lúc treo không được hẹn, và alpha chỉ kết thúc ở lưới đỡ 45 giây.
    - Test vẫn đạt vì chưa kiểm pha/thời gian của alpha.
    - Đã sửa (lớp ghi kế thừa lớp thật `RUN_STOP`) và thêm hai kiểm tra trên.
  - Kiểm ngược (trong bộ nhớ của tiến trình test), cả ba đều làm test FAIL:
    - `close` không hủy timer → test lượt bình thường FAIL;
    - `_kill` không làm gì → test treo FAIL (watchdog 60 giây);
    - bỏ qua `_stop("timeout")` → test treo FAIL.

**2. Race của `SpaceTests.test_waits_for_space_then_downloads`.**
- Worker đặt WAITING_SPACE trước, rồi `_wait_for_space` mới ghi "Chờ chỗ trống". Test đọc thông báo ngay khi thấy trạng thái.
- Bằng chứng: chạy test gốc với lần đọc dung lượng đầu chậm 0,3 giây cho 3/3 `TypeError: argument of type 'NoneType' is not iterable`.
- Sửa: chờ đúng điều kiện (WAITING_SPACE và thông báo có "Chờ chỗ trống"), vẫn giữ kiểm nội dung. Không sleep cố định, không đổi scheduler.
- Sau sửa: 3/3 đạt với lần đọc chậm; cả lớp 3/3.

**Kết quả lần 1 (trước prompt bổ sung):**
- Hai module đã sửa (`test_download_worker`, `test_download_account_hang`): 37 test OK (141 giây). Module hang chạy riêng 8/8 (131 giây).
- Regression độc lập của Codex, chạy tại chỗ, không sửa: M4 3/3, M3 4/4, M2b 2/2.
- Module hang với 4 tiến trình đốt CPU (chỉ để lấy bằng chứng): 8/8 đạt; lượt bình thường 3,6–4,2 giây.
- Full suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree, không tạo tải nhân tạo, watchdog ngoài 60 phút; log `full-suite-phaseA.log` trong scratchpad của phiên): **2.380 test trong 1.206 giây: 2.353 OK, 26 skip, 1 ERROR. Chưa đạt cổng.**
  - ERROR: `test_download_worker_robustness.SharedSpaceTests.test_a_second_download_counts_the_space_the_first_one_still_needs`, dòng 179, `TypeError: argument of type 'NoneType' is not iterable`.
    - Đây là test gốc, không đổi từ base, cùng kiểu race với SpaceTests: test đọc thông báo "… dành cho lượt đang tải" ngay khi lượt thứ hai vào WAITING_SPACE, trước khi `_wait_for_space` ghi.
    - Đường WAITING_SPACE trong `download_worker.py` giống base `e8aea11`.
    - Bằng chứng: chèn trễ 0,3 giây vào mỗi lần đọc dung lượng cho 3/3 cùng TypeError; chạy nguyên bản 10/10 đạt.
    - Theo chỉ dẫn, không sửa; dừng để Codex kiểm tra.
  - 26 skip có lý do:
    - 11 thiếu FFmpeg của project trong worktree;
    - 5 thiếu preview ident Golden;
    - 4 thiếu bộ nhớ logo studio của project;
    - 1 thùng rác thật (opt-in);
    - 1 không có DB Control Center thử;
    - 1 nhãn v1 không ở revision 469;
    - 1 thiếu bộ phân loại an toàn đã cài;
    - 1 thiếu Playwright cho kiểm trình duyệt điện thoại;
    - 1 thiếu dữ liệu phim thật.

    Không skip nào thuộc test tải.
- Dọn (sau khi tiến trình của chúng đã thoát):
  - xóa 20 thư mục `temp\session-browser-*` trong worktree do các lần lấy bằng chứng để lại (Edge còn giữ file lúc test dọn, hoặc lượt bị dừng);
  - kết thúc các tiến trình đốt CPU và script bằng chứng của chính phiên.

  Full suite không để lại thư mục tạm hay tiến trình.

**3. Lỗi cuối: race của `SharedSpaceTests` (prompt bổ sung, ngoài repo).**
- Codex tái hiện độc lập 3/3 lỗi ở `tests/test_download_worker_robustness.py:179` và giao sửa đúng test này (không sửa production hay scheduler).
- Nguyên nhân: như `SpaceTests`. `_wait_for_space` đặt WAITING_SPACE trước, ghi "… dành cho lượt đang tải" sau; test chỉ chờ trạng thái rồi đọc `error_message` còn None.
- Trước khi sửa: script độc lập của Codex (`download_account_phase_a_review.py`, mỗi lần đọc dung lượng chậm 0,3 giây, fake yt-dlp, root tạm) báo 3/3 tái hiện đúng TypeError.
- Sửa (chỉ test):
  - chờ điều kiện có cả WAITING_SPACE và thông báo "dành cho lượt đang tải" (`wait_for` với predicate), vẫn assert nội dung;
  - giữ: lượt đầu đang tải và giữ chỗ; lượt hai hoàn tất sau khi hủy lượt đầu;
  - thêm: lượt đầu vẫn DOWNLOADING khi lượt hai chờ; fake yt-dlp chỉ nhận lệnh tải của lượt đầu cho tới khi hủy, rồi đúng thêm lệnh của lượt hai.
  - Không sleep cố định, không bỏ assertion, không skip/retry.
- Sau sửa: script của Codex với `--expect-pass` 3/3 đạt dưới trễ 0,3 giây; chạy nguyên 10/10 đạt.
- Kiểm ngược trong bộ nhớ (không sửa file): bỏ phần dung lượng lượt đầu còn cần (`_others_need` = 0) → test FAIL vì lượt hai tải ngay; không ghi thông báo chờ → test FAIL ở điều kiện chờ.

**Kết quả cuối (code cuối, sau mục 3):**
- `SharedSpaceTests` + `SpaceTests` 4 OK; hai module worker (`test_download_worker`, `test_download_worker_robustness`) 44 OK.
- Regression độc lập của Codex, chạy tại chỗ, không sửa: M4 3/3, M3 4/4, M2b 2/2.
- Full suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree, Python/FFmpeg đã có, không tạo tải nhân tạo, watchdog ngoài 60 phút; log `full-suite-phaseA-last.log` trong scratchpad của phiên, 23:01–23:21): **2.380 test trong 1.193,9 giây: 2.354 OK, 0 FAILURE, 0 ERROR, 26 skip. Cổng A đạt.**
  - 26 skip có lý do, như lần 1: 11 thiếu FFmpeg của project trong worktree, 5 thiếu preview ident Golden, 4 thiếu bộ nhớ logo studio của project, 1 thùng rác thật (opt-in), 1 không có DB Control Center thử, 1 nhãn v1 không ở revision 469, 1 thiếu bộ phân loại an toàn đã cài, 1 thiếu Playwright (node) cho kiểm trình duyệt điện thoại, 1 thiếu dữ liệu phim thật. Không skip nào thuộc test tải.
  - Không còn thư mục tạm hay tiến trình của test sau khi suite thoát.

**Giới hạn khi CPU bị chiếm hết** (12 tiến trình đốt CPU trên 12 CPU logic, chỉ để đo; không phải kết quả full suite bình thường):
- Riêng bước đóng context Edge mất 18–38 giây.
- Lượt bình thường có lần vượt 45 giây (`RunTimedOut`), một lượt bị watchdog 60 giây chặn, và việc dọn hồ sơ gặp `PermissionError` vì Edge còn giữ file.
- Không nới hạn thêm để che: 45 giây đủ khi máy bận vừa. Khi CPU bị chiếm hết, các test Edge thật này có thể lỗi.
- Production không đổi (180 giây mỗi lượt).

Lần 1 dừng trước M5 vì còn 1 ERROR; sau mục 3, cổng A đạt trên code cuối. Không đổi code production, không cài gì, không chạy Control Center thật, không truy cập nguồn hay phiên thật, không sửa thư mục chính hay test độc lập của Codex.

### 9.17 M5 (2026-10-09): giao diện tài khoản nguồn phim, chọn tập và tiến độ nhóm

Người dùng giao M5 theo prompt riêng (phần B, ngoài repo), chỉ sau khi cổng A đạt (mục 9.16). M5 chỉ làm giao diện Dashboard V2 trên contract M4/9.15. Không đổi scheduler, lifecycle, worker hay route. Thay đổi duy nhất ngoài `dashboard_v2\` là `control_center.DASHBOARD_V2_FILES` có thêm `download-episodes.js`: đây là danh sách file tĩnh được phục vụ, không đổi route hay hành vi nào khác.

**File:**
- Mới:
  - `dashboard_v2/download-episodes.js`: hộp "Chọn tập";
  - `download-fake-accounts.cjs`: phần nguồn, trang phim nhiều tập và nhóm của server giả;
  - `verify-download-accounts.cjs`: gate node của M5.
- Sửa:
  - `contracts.js`: 11 endpoint mới; `{source}` chỉ nhận mã nguồn hợp lệ; `accountOps`, `PC_ONLY_ACCOUNT_REASON`;
  - `adapter.js`: `AdapterError.detail` chỉ giữ các trường trong danh sách; `loadEpisodes`, `loadGroup`; store có `accountAction`, `episodeDraft` (không làm mới danh sách), `episodeConfirm`;
  - `download-core.js`: nút theo trạng thái tài khoản, nhóm và tập;
  - `download-view.js`: khung tài khoản, thẻ nhóm, dòng lượt tải;
  - `download-live.js`: nối các phần;
  - `app.js` (1 dòng), `live.html` (1 thẻ script), `theme.css` (khối CSS M5);
  - `download-fake-server.cjs`;
  - `README.md` của `dashboard_v2`.
- Test Python:
  - `test_dashboard_v2_contract.py`: `{source}`; route tài khoản là PC-only trên listener điện thoại với đúng lý do của trang; route chọn tập và nhóm được dùng trên điện thoại;
  - `test_dashboard_v2_downloads.py`: chạy gate M5 (ít nhất 29 test đạt); `download-episodes.js` được phục vụ và tải trước `download-live.js`;
  - `test_dashboard_v2_review.py`: `ENDPOINTS_SHA256` mới, có ghi chú vì endpoint được thêm có chủ ý.

**Khung "Tài khoản nguồn phim"** (dưới khung Thêm video):
- Ô chọn nguồn lấy từ `accounts.sources`. Lựa chọn được giữ qua các lần làm mới. Nguồn không còn trong danh sách thì quay về nguồn đầu.
- Trạng thái chỉ lấy từ server: Chưa đăng nhập, Đang đăng nhập, Đã kết nối, Hết phiên (`SESSION_EXPIRED`/`CLOCK_CHANGED`), Cần đăng nhập lại, Chưa kiểm tra được kết nối (lỗi mạng, không phải hết phiên), Chưa hỗ trợ, Chưa sẵn sàng.
- Cửa sổ đã đóng, câu trả lời 202 hay `last_login` cũ không bao giờ thành "Đã kết nối". Lần đăng nhập thành công cũ nằm dưới một trạng thái khác thì ghi rõ phiên đó hiện không dùng được.
- Hiện thêm:
  - giờ đăng nhập;
  - "Phiên dùng tới … (còn khoảng N phút)", chỉ để tham khảo, với nguồn kiểm tra bằng TTL; câu kèm theo nói mở trang không gia hạn phiên;
  - lần kiểm tra gần nhất, với nguồn kiểm tra trực tiếp;
  - cảnh báo, lần đăng nhập gần nhất (đã lọc), số lượt chờ đăng nhập;
  - lỗi cấu hình (`problem_text`).
- Không có trường phiên, cookie, vé hay đường dẫn kho nào trên trang. Không lưu gì vào `localStorage`.
- Nút:
  - chỉ trên PC: Đăng nhập / Đăng nhập lại, Hủy đăng nhập khi đang mở, Ngắt kết nối khi có phiên;
  - nút chỉ hiện khi Control Center xác nhận trang mở trên PC (`/api/phone-mode` trả `remote: false`); đang chờ, lỗi hay câu trả lời thiếu `remote` là chưa xác định, không có nút (sửa sau review, mục 9.18);
  - nguồn chưa có bộ xác nhận (`login_supported: false`) hoặc chưa có bộ đọc trang (`reader_supported: false`) thì là "Chưa hỗ trợ", không có nút Đăng nhập (không mở cửa sổ vô ích);
  - POST chỉ khi bấm. Không POST khi tải trang, khi làm mới, khi khôi phục, khi thử lại hay khi thiếu phiên.
- Hộp Ngắt kết nối nêu tên nguồn và số lượt đang chờ đăng nhập. Nó nói lượt sau, và lượt nào cần lấy vé mới, sẽ chờ đăng nhập lại; file đang truyền không bị ngắt.
- Điện thoại: chỉ xem trạng thái, kèm ghi chú "chỉ làm trên PC".
- Server cũ chưa có phần này: một câu ngắn. Không có nguồn nào: câu hướng dẫn nêu `config/download_accounts.local.json` trên PC (mẫu `config/download_accounts.example.json`). Không có nguồn giả.
- Câu cũ "Không dùng cookie hay đăng nhập" được thay bằng: BiliFlow không dùng cookie hay tài khoản của trình duyệt; nguồn cần đăng nhập chỉ dùng phiên người dùng tự đăng nhập ở khung này.

**Dòng lượt tải:**
- `WAITING_LOGIN` (cả tập trong nhóm): nêu nguồn, lý do và cách xử lý.
  - Trên PC, nút "Đăng nhập <nguồn>" chỉ chọn nguồn trong khung và đưa focus tới ô chọn nguồn, không gửi gì. Focus không đặt lên nút Đăng nhập, để giữ Enter hay bấm đúp không mở cửa sổ đăng nhập.
  - Trên điện thoại: hướng dẫn sang PC.
  - `OTHER_ACCOUNT` giải thích về tài khoản Windows đã thêm lượt.
- `NEEDS_CHOICE` của trang phim nhiều tập có nút "Chọn tập" và không có ô đổi tên: tên nhóm lấy từ tên phim trong danh sách. Luồng chọn video cũ (`entries`) giữ nguyên.
- `EXPANDED` nêu nhóm và có "Xem nhóm". Nút duy nhất là "Xóa cả nhóm": nêu phạm vi và các file được giữ, chỉ bật khi nhóm đã xong. Nhóm đã hủy mà còn tập đang dừng thì lý do là "Nhóm đang hủy", không bảo bấm Hủy nhóm.
- Tập trong nhóm hiện nhóm, thứ tự/tổng, mã tập, tên file dự kiến và "Xem nhóm". Đổi tên chỉ sửa phần tên phim.
- Tập của nhóm đã hủy không có Tiếp tục/Thử lại. Lỗi 409 từ server vẫn được báo.

**Hộp "Chọn tập"** (`<dialog>` riêng, được vá tại chỗ):
- Mở ra chỉ đọc danh sách (GET), không POST.
- Mùa và tập giữ đúng thứ tự server ("Tập 10" sau "Tập 2").
- Phạm vi: "Tải tất cả các tập đang có" hoặc "Chọn tập". Có chọn/bỏ theo mục và chọn/bỏ tất cả.
- Bản tải: một bản cho mọi tập (`variant_kind`) hoặc bản riêng từng tập (`variants`). Tập không có bản đã chọn được bỏ ra để server báo thiếu; BiliFlow không đoán.
- Số tập, nhãn nút "Tải N tập" ("N tập đã thấy" khi danh sách chưa đủ), các tập thiếu hoặc mơ hồ và lời chặn đều lấy từ `plan`/`plan_error` của server. Danh sách chưa đủ thì phải tick ô xác nhận (`confirm_scope`).
- Lưu nháp:
  - gửi kèm `fingerprint`/`revision`, chờ 300 ms để gộp nhiều thao tác;
  - mỗi lúc chỉ một yêu cầu; thay đổi trong lúc chờ đi vào yêu cầu sau, kể cả khi hộp đã đóng;
  - đóng hộp thì gửi nốt lựa chọn chưa lưu;
  - mở lại hộp khi một nháp đang gửi thì chỉ đọc danh sách sau nháp đó (không hiện lựa chọn cũ hơn cái đã gửi);
  - lưu lỗi thì có nút "Lưu lại lựa chọn".
- Gặp `STALE_DRAFT` hay `STALE_PREVIEW`: mở lại lựa chọn hoặc danh sách đã lưu, kèm thông báo. Không ghi đè nháp của thiết bị khác, không tự gửi "Tải N tập". Tải lại danh sách lỗi thì báo, có nút thử lại, và chặn nút tải tới khi tải được.
- Mỗi 2 giây trang chỉ cập nhật thông báo: danh sách đổi, nháp đổi ở nơi khác, trang đã tách thành nhóm (khi đó có link tới nhóm), hoặc kết nối có lại (nút tải bật lại). Focus, vị trí cuộn và các ô đã tick được giữ.
- `idempotency_key` cố định cho mỗi yêu cầu (lựa chọn, fingerprint, `skip_existing`; như `download_groups.request_hash`):
  - bấm đúp hay gửi lại sau khi mất câu trả lời không tạo nhóm thứ hai;
  - gặp `IDEMPOTENCY_CONFLICT` thì bỏ khóa đó.
- `ITEMS_EXIST` liệt kê các tập đã có (server gửi tối đa 50; trang ghi "… và N tập khác") và cho "Tải các tập còn lại" (`skip_existing`, là một yêu cầu khác). Khi mọi tập đã chọn đều đã có thì không có nút đó, vì nó chỉ bị từ chối lại. `GROUP_TOO_LARGE`, thiếu bản, mơ hồ và nháp cũ đều có câu riêng.
- Xong thì đóng hộp, báo "Đã tạo nhóm" hoặc "Nhóm này đã được tạo trước đó", rồi đưa thẻ nhóm vào tầm nhìn và cho nó focus (chỉ trong 10 giây sau lệnh). Câu trả lời đến sau khi hộp đã đóng thì báo bằng toast.

**Nhóm tập** (mục "Nhóm tập (N)" giữa thanh công cụ và danh sách):
- Thẻ nhóm có: N/tổng tập đã xong, chip theo các bucket của server, trạng thái nhóm, ghi chú danh sách chưa đủ và tập đã có.
- Trạng thái "Đã xong" chỉ khi mọi tập đã vào input. Còn tập lỗi, dừng, bị ngắt hay đã dọn file tạm (thử lại được) thì "Cần xử lý"; còn tập đã hủy hay đã xóa thì "Xong một phần".
- Câu giải thích "chờ chỗ trong danh sách": danh sách giữ tối đa 100 lượt chưa xong và thêm dần theo thứ tự.
- Phần trăm và `aria-valuenow` chỉ khi server trả `percent` là số.
- Nút theo contract: Dừng nhóm, Tiếp tục nhóm, Thử lại tập lỗi, Hủy nhóm (nhóm đã hủy: "Hủy các tập còn lại"), Xóa nhóm khỏi danh sách (chỉ khi nhóm đã xong). Hộp xác nhận nêu số tập bị ảnh hưởng và file nào được giữ; Hủy nhóm nói tập đang chuyển vào input thì vẫn xong.
- Mở "Các tập theo thứ tự" thì hiện "Đang tải danh sách tập…" ngay, tải `GET …/groups/<id>`, tải lại khi tóm tắt đổi hoặc mỗi 4 giây khi nhóm còn chạy.
- Không có pause toàn cục; số luồng 2/3 giữ nguyên.

**Dữ liệu không tin được:**
- Mọi chuỗi từ nguồn hay server đều qua `esc()` trong chữ và thuộc tính; markup tham chiếu tập theo vị trí, không theo mã của nguồn.
- Nhãn và màu của trạng thái chỉ tra theo khóa riêng của bảng: trạng thái tên `constructor` vẫn hiện là chữ.
- Chỉ adapter gọi HTTP, POST có token, câu trả lời cũ đến muộn bị bỏ.
- Thiếu trường (server cũ), GET lỗi, 503, cấu hình sai hay mục danh sách bị méo đều hiện an toàn. Trình duyệt không có `crypto.getRandomValues` thì không gửi và nói lý do.

**Kiểm tra trên code cuối:**
- Gate node: `verify` 38/0, `verify-adapter` 37/0, `verify-download` 22/0, `verify-download-accounts` 29/0 (mới), `verify-review` 32/0.
- Kiểm ngược:
  - trên bản sao tạm trong `temp\`, mỗi thay đổi phải làm gate FAIL; 5/5 FAIL: gửi lại với khóa mới; hai nháp chồng nhau; nhãn nguồn không escape; nút tài khoản trên điện thoại; phần trăm nhóm tự bịa;
  - test thêm sau review chạy trên bản sao code trước khi sửa (scratchpad của phiên): 11/11 test bị đổi hay thêm đều FAIL, 18 test còn lại đạt.
- Test Python của Dashboard V2 (contract, downloads, frontend, phone, phone hardening, review, route, status), `test_download_account_api`, `test_download_groups`, `test_download_group_intents` trên code cuối: 157 test: 156 đạt + 1 skip (kiểm trình duyệt điện thoại cần Playwright cho node, có từ trước).
- Trình duyệt thật: Edge headless qua Playwright đã có trong venv (không cài gì), với `download-fake-server.cjs` trên cổng riêng, không Control Center thật. Trên code cuối: 78/78 mục đạt. Đã kiểm:
  - PC sáng 1440, PC tối;
  - điện thoại 390 sáng và 375 tối, qua listener điện thoại giả;
  - server cũ không có phần tài khoản; không có nguồn nào;
  - không POST khi tải trang và qua hai lần làm mới; không mồi XSS nào chạy hay chèn phần tử;
  - CTA của lượt chờ đăng nhập chỉ chọn nguồn và đưa focus tới ô chọn nguồn;
  - đăng nhập: một POST mỗi lần bấm. "Đang đăng nhập" theo poll. Đóng cửa sổ thì vẫn "Hết phiên", kèm dòng "đóng trước khi đăng nhập xong". Đăng nhập xong thì "Đã kết nối" và lượt chờ chạy lại. Hộp Ngắt kết nối hủy được mà không gửi gì;
  - nhóm: phần trăm, thứ tự tập, hộp Hủy nhóm, mỗi lần bấm Dừng/Tiếp tục là một POST;
  - hộp chọn tập:
    - thứ tự server;
    - bản mơ hồ chặn nút;
    - ba thao tác nhanh thành một nháp;
    - focus và vị trí cuộn giữ qua poll;
    - Tab ở trong hộp; Escape trả focus về nút "Chọn tập";
    - nháp còn khi mở lại;
    - nháp của thiết bị khác chỉ báo, không áp;
    - `STALE_DRAFT` mở lại nháp đã lưu;
    - bấm đúp chỉ gửi một yêu cầu, một nhóm, thẻ nhóm nhận focus;
  - `ITEMS_EXIST` cùng "Tải các tập còn lại" khi câu trả lời bị mất: trang báo "Mất kết nối khi gửi", bấm lại thì server trả lại đúng nhóm đã tạo; vẫn chỉ một nhóm 10 tập;
  - 500 tập: mở và chọn hết đều dưới 5 giây (đo được khoảng 0,1 giây), phải tick ô phạm vi; nhóm 500 tập với 443 tập "chờ chỗ trong danh sách" dưới trần 100;
  - điện thoại: không nút tài khoản, không cuộn ngang, hộp chọn tập vừa màn hình.
- Ảnh chụp trong scratchpad của phiên (`m5-shots\`):
  - PC sáng: `pc-light-overview`, `-accounts`, `-group`, `-dialog`, `-dialog-500`, `-accounts-none`, `-after`;
  - PC tối: `pc-dark-overview`, `-accounts`, `-group`, `-dialog`;
  - điện thoại: `phone-390-light*`, `phone-375-dark*` (trang, khung tài khoản, thẻ nhóm, lượt chờ đăng nhập, hộp chọn tập);
  - kết quả từng mục ở `results.json`.
- Tìm được khi kiểm trên trình duyệt:
  - Chân hộp chọn tập trong suốt 96% (kiểu chung) nên các tập cuộn bên dưới lộ ra. Đã cho chân hộp này nền đặc theo theme.
  - Ghi chú nhóm có hai dấu chấm liền nhau. Đã sửa.
  - Nếu kết nối bị cắt trước khi có header, chính Edge tự gửi lại POST trên socket dùng lại; khóa `idempotency_key` biến lần gửi đó thành trả lại nhóm cũ (đã đo, vẫn một nhóm). Server giả vì vậy có hai kiểu mất câu trả lời: `cut` (để kiểm đường của trang) và `reset`.
- Review chỉ đọc bằng ba agent; không có CRITICAL hay HIGH:
  - Bảo mật: không có MEDIUM. Đã sửa L1 (CTA đưa focus tới nút Đăng nhập), L2 (nút tài khoản hiện thoáng qua trên điện thoại trước khi biết chế độ), L3 (`Number()` cho id trong selector), L4 (tra bảng nhãn/màu theo khóa kế thừa) và I2 (thiếu `crypto` không báo; mục danh sách bị méo làm lỗi vẽ). I1 để Codex và người dùng quyết định (xem giới hạn).
  - Đối chiếu yêu cầu: đã sửa MEDIUM-1 (tập trong nhóm ở `WAITING_LOGIN` mất CTA và hướng dẫn) và LOW 1, 2, 4–8 (bộ đọc trang, đường dẫn cấu hình, "… và N tập khác", câu Ngắt kết nối, lần đăng nhập cũ, câu Hủy nhóm, ô đổi tên của trang phim). MEDIUM-2 là bằng chứng trình duyệt và mục này. LOW-3 giữ nguyên: ghi chú danh sách chưa đủ chỉ hiện `listing.message`, vì `reasons` là mã máy.
  - JavaScript: đã sửa cả 4 MEDIUM (mở lại hộp khi nháp đang gửi làm mất thay đổi; nút tải không bật lại khi có mạng lại; "Tải các tập còn lại" vô ích khi mọi tập đã có; tải lại lỗi không báo) và phần lớn LOW (toast cho câu trả lời đến sau khi đóng hộp, "Lưu lại lựa chọn", đọc danh sách tuần tự, trạng thái nhóm, lý do "Nhóm đang hủy", dấu thiếu bản dựng một lần mỗi lượt vẽ, hạn 10 giây cho focus thẻ nhóm, "Đang tải" khi mở danh sách tập). Không sửa: xem giới hạn.
- Full suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree): 2.382 test trong 1205,9 giây, 2.356 OK, 0 FAILURE, 0 ERROR, 26 skip có lý do (log `m5-full.log` trong scratchpad của phiên). Không còn tiến trình nào của test; các server giả và Edge của kiểm tra trình duyệt đều đã dừng.

**Giới hạn:**
- Chưa nguồn thật nào có bộ đọc trang hay bộ xác nhận đăng nhập (M7). Nút Đăng nhập chỉ được kiểm với server giả; cửa sổ đăng nhập thật chưa được người dùng thử.
- Kiểm tra trình duyệt dùng server giả, không dùng Control Center thật.
- Test `browser-check-review-phone` vẫn bỏ qua vì máy không có Playwright cho node (có từ trước).
- I1 của review bảo mật (backend, chưa đổi): `accounts.problem_text` có thể chứa tên host thật từ `config/download_accounts.local.json`. Nó đi qua `GET /api/downloads` nên điện thoại và thiết bị Tailscale cũng thấy. Đây không phải bí mật và trang đã escape; giữ hay bỏ là việc của Codex và người dùng. Review M5: Codex quyết định giữ (tên host không phải cookie, vé hay token), vẫn phải lọc bí mật như các mốc trước.
- "Tải N tập" vẫn chờ lần làm mới danh sách sau khi gửi (tối đa bằng giới hạn GET 15 giây) rồi mới đóng hộp.
- Danh sách tối đa 1.000 tập (`MAX_EPISODES` của bộ đọc; một nhóm tối đa 500): ở chế độ bản riêng từng tập, mỗi lần vẽ dựng lại cả danh sách (đo được khoảng 24 ms cho 1.000 dòng, chưa tính lúc trình duyệt dựng DOM).
- `loadedAt`/`loadedState` của dòng lượt tải chưa được dọn (có từ trước M5).
- Chưa commit, merge hay push. Dừng để Codex review; chưa làm M6/M7. Codex review có một P2, đã sửa ở mục 9.18.

### 9.18 Sửa sau review M5 của Codex (2026-10-09)

Codex review M5 (`BILIFLOW-SOURCE-ACCOUNTS-M5-REVIEW.md`, ngoài repo) có một P2: trang mở qua listener điện thoại mà GET `/api/phone-mode` lỗi (503 hay 404) thì vẫn hiện Đăng nhập lại và Ngắt kết nối. Backend vẫn chặn vì các route này là PC-only, nhưng giao diện sai quy tắc "điện thoại chỉ xem trạng thái tài khoản". Prompt sửa (ngoài repo) yêu cầu chỉ bật thao tác tài khoản sau một câu trả lời thành công xác nhận `remote === false`, và không đổi provider, scheduler hay backend.

**Nguyên nhân:**
- `adapter.loadPhone()` ghi `phone: {unavailable: true}` khi GET lỗi, còn `remote` của snapshot vẫn là `false` (giá trị mặc định).
- `download-live.ctx()` dùng `modeKnown: s.phone != null`, nên coi chính kết quả lỗi đó là đã biết chế độ. `download-core.accountActions` vì vậy trả các nút PC.

**Cách sửa** (chỉ trong `dashboard_v2\`):
- Chế độ của trang:
  - Store có `device`: `'pc'`, `'phone'` hoặc `null`. Chỉ câu trả lời có `remote` kiểu boolean đặt nó (`false` là PC, `true` là điện thoại). Câu trả lời của Bật/Tắt và Gia hạn chế độ điện thoại cũng theo quy tắc này.
  - Đang chờ, lỗi HTTP hay mạng, quá giới hạn GET 15 giây, thiếu `remote` hay `remote` không phải boolean đều giữ giá trị đã biết. Lúc đầu giá trị đó là chưa xác định.
  - Lỗi không bao giờ biến điện thoại thành PC. Một trang không đổi listener, nên PC đã xác nhận vẫn là PC.
  - Không đoán theo URL, IP, kích thước màn hình hay user-agent.
- Ba lớp cùng chặn, backend PC-only giữ nguyên:
  - `accountActions` chỉ trả nút khi `device === 'pc'` (bỏ `modeKnown`);
  - khung tài khoản và dòng chờ đăng nhập không có nút;
  - `store.accountAction` từ chối trước khi gửi nếu trang chưa được xác nhận là PC.
- Khi chưa xác định:
  - Khung tài khoản ghi "Chưa xác định được trang này mở trên PC hay qua điện thoại", kèm lý do ngắn (mất kết nối hoặc quá thời gian chờ, 404 có thể là bản cũ, mã lỗi, câu trả lời thiếu thông tin). Không lộ chữ của server.
  - Nút "Kiểm tra lại" chỉ GET `/api/phone-mode`, không POST, và bị tắt khi đang kiểm. Lần kiểm đầu và khi bấm Kiểm tra lại hiện "Đang kiểm tra…". Lần thử lại tự động sau một lỗi giữ nguyên lỗi trên trang, không nhấp nháy.
  - Poll tự hỏi lại tối đa 5 lần, chỉ GET, mỗi lần một yêu cầu. Lượt tick khi yêu cầu cũ còn treo không bị tính. Poll dừng ngay khi biết chế độ, và được thêm 5 lần khi có mạng lại sau lúc hết lượt.
  - Câu trả lời có `enabled` mà thiếu `remote` không còn làm poll của khung chế độ điện thoại trên PC chạy mãi: việc tải lại khung đó cần PC đã xác nhận.
- Dòng `WAITING_LOGIN` (cả tập trong nhóm) khi chưa xác định: không có nút, chỉ một câu đúng trên mọi thiết bị: "đăng nhập trên PC (trang Tải video → Tài khoản nguồn phim), lượt này tự tiếp tục sau đó". Câu này khớp gợi ý của server. Câu `OTHER_ACCOUNT` nói rõ mở BiliFlow trên PC.
- Badge đầu trang Tải video: "Qua điện thoại", "Control Center" (PC đã xác nhận) hoặc "Chưa rõ PC hay điện thoại". Chip "CONTROL CENTER" ở thanh trên là nhãn live/demo có từ trước và hiện trên mọi thiết bị; nó không phải nhãn PC.
- Sau khi một tác vụ Tailscale xong, trang đọc lại chế độ bằng một yêu cầu mới, không dùng chung yêu cầu đã gửi trước khi tác vụ xong.
- Câu "phiên đó hiện không dùng được" dưới lần đăng nhập gần nhất chỉ hiện ở Chưa đăng nhập và Cần đăng nhập lại, không hiện lúc Đang đăng nhập.

**File:** `adapter.js`, `download-core.js`, `download-live.js`, `download-view.js`, `app.js` (badge), `theme.css` (khối `.dl-mode-unknown`), `verify-download-accounts.cjs`, `tests/test_dashboard_v2_downloads.py` (ngưỡng 42 test của gate).

**Test:**
- `verify-download-accounts.cjs` tăng từ 29 lên 42 test.
  - 13 test "page mode" chạy đúng đường thật: `adapter.create` → `createLiveStore` → bộ điều khiển `download-live`. HTML của trang được đọc lại, và mỗi lần bấm đi qua handler của nó.
  - Các trường hợp: đang chờ; 503; 404; mất kết nối; quá thời gian (qua `fetchTransport` thật, giới hạn 30 ms); thiếu `remote`; `remote` dạng chữ; body rỗng.
  - Kiểm tra lại đưa về `remote=false`: nút chạy, mỗi lần bấm một POST, Ngắt kết nối hỏi trước. Đưa về `remote=true`: không có nút và bấm không gửi gì. Lỗi sau đó vẫn giữ câu trả lời.
  - Hủy đăng nhập trên PC, trên điện thoại, sau lỗi và lúc đang chờ. Trên trang chưa xác định hay điện thoại, trang tự từ chối lượt bấm (không có toast lỗi của store).
  - Giới hạn 1 + 5 lần hỏi, cả khi câu trả lời có `enabled` mà thiếu `remote` và khi một yêu cầu còn treo; được hỏi thêm khi có mạng lại.
  - Câu trả lời POST thiếu `remote`; đọc lại sau tác vụ Tailscale.
  - Số lần hỏi được đếm sau một điều kiện chờ, không trong một khung giờ cố định. Mỗi test có hạn 10 giây, và gate chỉ thoát với mã 0 khi in được dòng tổng.
- Kiểm ngược trên bản sao trong scratchpad:
  - lượt sửa đầu: 7 test đổi hay thêm FAIL trên code trước khi sửa, 27 test còn lại đạt;
  - lượt sửa sau review: 5 test mới hay đổi FAIL trên code của lượt đầu;
  - cả 6 đột biến đều làm gate FAIL: bỏ điều kiện `!phoneRequest`; trả Gia hạn về nghĩa cũ; bỏ hạn GET (gate báo tên test sau 10 giây, mã thoát 1); bỏ chặn của bộ điều khiển cho Đăng nhập và Hủy đăng nhập; bỏ `fresh` ở Tailscale; bỏ nối `fresh`.
- Script độc lập của Codex `download_account_m5_mode_review.py --expect-pass`: chạy bản sao byte-giống (SHA-256 `cfbdb69cc2b4…`) trong scratchpad để không ghi đè ảnh của Codex. Kết quả: normal, 503 và 404 đều không có nút tài khoản, mã thoát 0. Script không bị sửa.
- Kiểm trình duyệt riêng (`m5_mode_browser.py` trong scratchpad): Edge headless qua Playwright đã có, `download-fake-server.cjs` ở cổng 8831 (PC) và 8832 (`--phone`), chỉ chặn GET `/api/phone-mode`, không Control Center thật. Kết quả 59/59:
  - đang chờ rồi `remote=false`: nút hiện, mỗi lần bấm một POST;
  - 503 rồi Kiểm tra lại về PC; một 503 sau đó giữ PC;
  - mất kết nối: tối đa 1 + 5 lần hỏi, chỉ GET;
  - thiếu `remote`; GET bị cắt sau 15 giây;
  - điện thoại 390: 404 rồi Kiểm tra lại về `remote=true`, một 503 sau đó không biến nó thành PC;
  - điện thoại 375 tối với 503;
  - ở mọi trường hợp chưa xác định: badge không nhận là PC, ép bấm ba thao tác tài khoản vẫn không gửi POST, không có hộp Ngắt kết nối, không cuộn ngang, không lỗi trang.
- Ảnh trong scratchpad của phiên (`m5-shots-modefix2\`): `mode-pending`, `mode-pc-confirmed`, `mode-503`, `mode-timeout`, `mode-phone-recovered`, `mode-phone-375-dark-503`; mỗi trường hợp có ảnh cả trang, `-accounts` và `-heading`. Ảnh từ script của Codex ở `m5\codex-mode-fix2\`.
- Gate node: `verify` 38/0, `verify-adapter` 37/0, `verify-download` 22/0, `verify-download-accounts` 42/0, `verify-review` 32/0.
- Test Python của Dashboard V2 (contract, downloads, frontend, phone, phone hardening, review, route, status), `test_download_account_api`, `test_download_groups`, `test_download_group_intents`: 157 test: 156 đạt + 1 skip (Playwright cho node, có từ trước).
- Full suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree): 2.382 test trong 1142,9 giây, 2.356 OK, 0 FAILURE, 0 ERROR, 26 skip có lý do (log `m5-full-2.log` trong scratchpad của phiên). Không còn tiến trình nào của test; các server giả và Edge của kiểm tra trình duyệt đều đã dừng.

**Review:** một workflow gồm 4 agent chỉ đọc (yêu cầu, bảo mật, máy trạng thái, test) và một bước kiểm chứng đối kháng; không có CRITICAL hay HIGH.
- Lens test có một MEDIUM: Hủy đăng nhập chưa được thử trên đường thật. Đã thêm test; bước kiểm chứng hạ nó xuống LOW và chỉ ra hai trường hợp còn thiếu (điện thoại, đang chờ), cũng đã thêm.
- Các LOW đã sửa:
  - poll chạy mãi khi câu trả lời thiếu `remote`;
  - lượt thử lại bị tiêu khi mất mạng mà không được cấp lại;
  - đọc lại chế độ sau tác vụ Tailscale;
  - badge;
  - câu dòng chờ đăng nhập và câu `OTHER_ACCOUNT`;
  - lý do ngắn hơn;
  - test đếm theo khung giờ;
  - yêu cầu treo, toast, câu trả lời POST;
  - runner thoát 0 khi một test treo.
- Không sửa (có từ trước, ngoài sáu điểm của prompt):
  - một câu trả lời GET `/api/phone-mode` cũ có thể đè câu trả lời POST Bật/Tắt mới hơn trong khung Cài đặt (không ảnh hưởng `device`);
  - `app.js` còn dùng `state.remote` cho các thao tác PC-only khác;
  - khi chưa xác định, link "Dung lượng" vẫn ghi "Xóa video gốc / Lưu trữ" (Lưu trữ là PC-only, backend vẫn chặn). Hai việc sau thuộc việc rà `app.js` đã tách riêng.

**Giới hạn:**
- Codex quyết định giữ `accounts.problem_text`: tên host trong cấu hình không phải cookie, vé hay token. Vẫn lọc bí mật như các mốc trước.
- Các giới hạn khác của mục 9.17 giữ nguyên: chờ làm mới sau "Tải N tập" (tối đa giới hạn GET 15 giây), chi phí vẽ danh sách và `loadedAt`/`loadedState` chưa dọn.
- Sửa này chỉ là giao diện; chưa nguồn thật nào có bộ đọc trang hay bộ xác nhận đăng nhập (M7). Kiểm tra dùng server giả, không dùng Control Center thật.
- Chưa commit, merge hay push. Dừng để Codex review lại; chưa làm M6/M7.

### 9.19 M6 (2026-10-09): kiểm tích hợp bằng fixture

Prompt M6 (ngoài repo) giao:
- lập ma trận tích hợp;
- dùng lại test có ý nghĩa, chỉ thêm test cho chỗ hở;
- có ít nhất một luồng đầu-cuối qua Dashboard V2 với route thật và bộ tải thật, trên root fixture riêng;
- chạy các bộ kiểm tra và full suite.

Mốc này không dùng nguồn thật, phiên thật hay Control Center thật. M6 không chứng minh nguồn thật tải được (xem "Cho M7").

**Cách chạy và dừng**
- Môi trường (Git Bash, từ worktree): `PYTHONPATH="<worktree>\src;<worktree>"`, `TEMP` và `TMP` là `E:\DungChung\BiliFlow\temp`, `BILIFLOW_FFMPEG` là FFmpeg của project, `PYTHONIOENCODING=utf-8`.
- Cần Playwright và Edge đã cài; không tải gì về.
  - Thiếu một thứ thì test được báo skip, và lý do nêu đúng thứ thiếu.
  - Đặt `BILIFLOW_REQUIRE_E2E=1` thì thiếu là lỗi (full suite của M6 chạy với biến này).
- Harness từ chối chạy khi `biliflow` hay `dashboard_v2` không phải của worktree, hoặc khi thư mục tạm nằm ngoài `E:\DungChung\BiliFlow` (`check_tree`).
- Lệnh: `E:/DungChung/BiliFlow/.venv/Scripts/python.exe -m unittest tests.test_download_account_e2e tests.test_download_account_e2e_flows tests.test_download_account_crossing -v`. Mất khoảng 10 phút trên Edge thật. Docstring của `tests/test_download_account_e2e.py` ghi đủ lệnh, root, cổng và cách dọn.
- Root và cổng riêng:
  - mỗi test tạo `<worktree>\temp\m6-e2e-*`, gồm DB tải, DB Control Center, kho phiên giả, `input\` và `temp\`;
  - profile Edge của dashboard nằm ở `m6-dash-*`; mỗi module có `m6-clips-*`; CA dùng một lần nằm ở `session-browser-tls-*`;
  - mọi listener bind `127.0.0.1:0`: handler Control Center (token ngẫu nhiên mỗi test), listener điện thoại, server HTTPS fixture và proxy chặn của trình duyệt phiên;
  - Control Center thật (8765), cấu hình, DB, kho phiên và `input\` của bản chính không bị đụng.
- Dừng: Ctrl+C.
  - Cleanup của test đóng dashboard, dừng service (có hạn) và đóng listener. Nó chờ Edge của root tắt, rồi xóa các thư mục trên sau khi kiểm đường dẫn tuyệt đối nằm trong `temp`.
  - Tài nguyên của module được đăng ký bằng `addModuleCleanup` ngay khi tạo, nên setup lỗi giữa chừng cũng không để lại thư mục.
  - Sau khi bị kill cứng: liệt kê chỉ đọc (`Get-CimInstance Win32_Process -Filter "Name='msedge.exe'"`), chỉ dừng tiến trình có command line chứa `m6-e2e-` hay `m6-dash-`, rồi chỉ xóa các thư mục đó.
- Ảnh: `BILIFLOW_E2E_SHOTS=<thư mục tuyệt đối ngoài repo>`. Thư mục phải trống hoặc có file đánh dấu của harness. Test lưu PNG (PC sáng/tối, điện thoại 390 sáng và 375 tối, các trạng thái chưa xác định chế độ và đăng nhập lỗi) và `results.json`. File này bị từ chối nếu chứa bí mật.

**Harness** (`tests/account_e2e_fixtures.py`, chỉ có helper):
- Handler: handler thật `_handler_class` của một `ControlCenter.__new__`, có lớp con ghi mọi request trước khi byte trả lời đầu tiên được gửi, kèm Set-Cookie và Location. AI status, AI audit và Tailscale là stub của instance. Scheduler là mock autospec không bao giờ chạy.
- Bộ tải: `DownloadService` thật (`start()` chạy luồng dispatch thật), `DownloadWorker`, `DownloadStore`, `AccountRuntime` và `AccountManager` thật. Kho phiên dùng protector và ACL giả của một SID giả. Đồng hồ đóng băng ở thời điểm thật.
- Hạn thời gian: dùng đúng hạn của production. Lượt ẩn 180 giây, cộng 10 giây chờ thêm và 30 giây mỗi trang; đăng nhập 600 giây. Edge của dashboard có 240 giây để khởi động và 60 giây cho mỗi thao tác.
- Provider: `AccountSourceProvider` thật của alpha và beta, với `FixtureReader` trên các `FilmSite` của một `FixtureServer` HTTPS. Mọi yêu cầu đi qua `SessionNetwork`/`SafeHttp`, với resolver, connector và TLS context được tiêm.
  - Các kiểm tra địa chỉ công khai, redirect, giới hạn byte, host và cookie là của production.
  - Địa chỉ "công khai" của fixture là một literal không bao giờ được kết nối thật.
- Đăng nhập: bộ điều phối thật với launcher `SignInWindows`, có ba kiểu:
  - `edge`: bản headless của cửa sổ, cùng `SessionBrowser` nhưng bỏ HeadedPermit nên không bao giờ hiện;
  - `fake`: `FakeWindow`;
  - `refuse`.
- yt-dlp giả (log gọi phải vắng), `verify_video` của production và disk probe được tiêm. Dự trữ 100 GB thật của ổ E: không phải đối tượng kiểm.
- Edge headless (Playwright, kênh `msedge`, `--no-proxy-server`) mở Dashboard V2. Listener điện thoại thật chạy trên 127.0.0.1 (chỉ stub bộ kiểm địa chỉ bind và cổng) và được đăng nhập bằng cách gõ mã.
- `DOM_WATCH` (init script chỉ của test) ghi theo thứ tự mọi thay đổi của:
  - số nút tài khoản và chữ badge;
  - mọi `<img>` lạ;
  - mọi vi phạm CSP.

  `look()` buộc một lần ghi mới. Vì vậy "không bao giờ hiện" phủ cả khoảnh khắc giữa hai lần nhìn và không thể đạt khi không ghi được gì.
- Bí mật được quét theo danh sách động: dấu cố định, cộng mọi cookie và vé site fixture đã cấp (`FilmSite.issued`), cookie xoay, mã và cookie của điện thoại. Phạm vi quét:
  - mọi JSON trả lời của hai listener, header đã ghi, ảnh chụp DOM;
  - URL request, body POST và console của trang;
  - mọi file của root, kể cả file `session-*` của kho phiên: không có bản rõ, không còn `.tmp`;
  - `results.json`.
- Mạng: `assert_network_kept(exact=True)`.
  - Tập host session browser tra phải đúng bằng tập mong đợi.
  - Có request thì phải có kết nối qua địa chỉ đã kiểm.
  - Không cookie nào tới host file.
- Route: chỉ POST tới các route trong danh sách viết tay. `RouteInventoryTest` giữ kho route POST của production trong một danh sách viết tay, không route nào có "pause", và mọi mẫu được phép đều khớp một route thật. Không có lỗi 5xx, không có request nào không được trả lời, không file `dashboard_v2` nào 404.
- Không thêm route, không bật nguồn giả trong production, không nới kiểm tra nào: reader, verifier, launcher và mạng của fixture chỉ là tham số constructor trong test. `LOGIN_VERIFIERS` và `PAGE_READERS` được kiểm vẫn rỗng trước và sau mỗi module.

**Ma trận tích hợp**

Mức bằng chứng:
- **E2E**: Dashboard V2 trên Edge headless → handler thật của Control Center (PC hoặc listener điện thoại) → `DownloadService`, worker, store, `AccountRuntime`, callback của bộ điều phối và `AccountSourceProvider` thật, trên root `m6-e2e-*` với site fixture HTTPS.
- **NỐI THẬT**: route, worker, store và manager thật, không có trang.
  - Các test của `test_download_account_queue` thay `resolve` của provider bằng `QueueProvider` (danh sách giả) và bộ kiểm file giả.
  - Các test crossing (C1) dùng provider thật và `verify_video` thật.
- **ĐƠN VỊ**: một module, hoặc thư viện chạy trên Edge headless thật nhưng không có worker hay route.
- **FAKE UI**: gate node (`verify-download-accounts.cjs` là VDA, `verify-download.cjs`). Các gate chạy module frontend thật với transport và document giả, không HTTP. Đây chỉ là bằng chứng giao diện.

Tên tắt:
- `tests/test_download_account_e2e.py`: E1 PC, E2 điện thoại, E3 chế độ chưa rõ và đăng nhập lỗi.
- `tests/test_download_account_e2e_flows.py`: E4 hết phiên giữa nhóm, E5 danh sách chưa đủ và ITEMS_EXIST, E6 không nguồn hay nguồn chưa hỗ trợ, E7 "Tải tất cả" và mồi HTML, cùng `RouteInventoryTest`.
- `tests/test_download_account_crossing.py`: C1a phim lẻ, C1t vé hết hạn hay file bị từ chối, C1e mã lỗi, C1r lượt trình duyệt nối tiếp.
- `tests/test_download_account_queue.py`: Q1–Q8 và các test route khác.
- T: `tests/test_download_account_transfer.py`.
- G: `tests/test_download_groups.py`.

| Nhóm | Yêu cầu (M6 §2 và mục 9.10) | Test đóng yêu cầu | Mức | Trạng thái |
|---|---|---|---|---|
| Tài khoản | Chưa có phiên: chưa kết nối; trang đã dán chờ WAITING_LOGIN/NOT_CONNECTED, không giữ slot, không vòng lặp | E1(a), E4; `WaitingLoginTest` | E2E + NỐI THẬT | Đủ |
| | Một lần bấm là một POST đăng nhập; chỉ bằng chứng của verifier mới thành CONNECTED; đánh thức theo nguồn, SID và generation, giữ chỗ trong hàng và trong nhóm | E1(b), E3(2), E4, Q7; `CoordinatorTest`, `SignInFlowTest` | E2E + ĐƠN VỊ | Đủ |
| | Hủy, đóng cửa sổ, lỗi mạng: không bao giờ CONNECTED (DOM watcher), tác vụ vẫn chờ, callback không đánh thức gì | E3(2)(3)(4); test login | E2E + ĐƠN VỊ | Đủ (ngữ nghĩa R17 ở Giới hạn) |
| | Chế độ trang chưa xác định (503, 404, mất kết nối, thiếu `remote`, quá giờ): không nút, không POST tài khoản | E3(1); VDA page mode | E2E + FAKE UI | Đủ; việc store và controller tự từ chối chỉ có ở FAKE UI |
| | Điện thoại chỉ xem tài khoản: 403 `pc_only` trước khi đọc body, cả khi PC đang đăng nhập; điện thoại vẫn dùng Chọn tập và các nút nhóm | E2, Q4; `test_the_phone_cannot_cancel_or_disconnect_while_a_pc_sign_in_runs` | E2E + NỐI THẬT | Đủ |
| | Không có nguồn hoặc nguồn chưa hỗ trợ: hướng dẫn đúng; không manager, không kho phiên; LOGIN_UNSUPPORTED trước mọi cửa sổ; READER_UNSUPPORTED; không sang yt-dlp, trình duyệt hay mạng | E6(a)(b) | E2E | Đủ |
| Phiên | TTL 3.600 giây tính từ `authenticated_at` (đồng hồ giả, mốc 3599/3600); đọc trang, poll và xoay cookie không gia hạn | `TtlTest`, `SignInFlowTest`, E4, C1r | ĐƠN VỊ + E2E | Đủ |
| | Hết phiên giữa nhóm: tập cần phiên thì chờ (giữ phần đã tải), lượt đang tải chạy tiếp, nguồn khác và SID khác không bị ảnh hưởng; đăng nhập mới thì nối tiếp đúng thứ tự, cùng bản | E4, Q5; `AccountScopeTest` | E2E + NỐI THẬT + ĐƠN VỊ | Đủ |
| | Restart giữ phiên còn hạn, chốt lượt đăng nhập dở, không mở cửa sổ | Q6; `RestartTest` | NỐI THẬT + ĐƠN VỊ | Đủ |
| Phim lẻ | Một bản thì tải thẳng; nhiều bản thì hỏi bản, chỉ tải bản đã chọn | C1a | NỐI THẬT (provider thật) | Đủ; giao diện chọn bản của phim lẻ chỉ có ở FAKE UI |
| Phim bộ | ≥12 tập, 2 mùa, tập đặc biệt, tập không số; phân trang đủ và thiếu; bản chung và bản theo tập; thiếu bản, bản mơ hồ; bỏ trailer và quảng cáo; giữ thứ tự server; một bản mỗi tập | E1, E5; test listing/sources | E2E + ĐƠN VỊ | Đủ |
| Chọn tập | Mở hộp chỉ GET; nháp gửi nối tiếp, có revision và fingerprint, giữ qua refresh và restart; lượt dispatch xen giữa lúc lưu danh sách không làm mất danh sách | E1(c)(e), E2, Q6; `test_a_dispatch_pass_between_storing_the_list_and_the_choice_keeps_the_list` | E2E + NỐI THẬT | Đủ |
| | STALE_DRAFT và STALE_PREVIEW không tự xác nhận, không đè thiết bị khác | E2 (STALE_DRAFT giữa PC và điện thoại); `test_a_list_read_again_refuses_the_old_fingerprint_on_draft_and_confirm` (STALE_PREVIEW qua route); VDA | E2E + NỐI THẬT + FAKE UI | Đủ; phần giao diện của STALE_PREVIEW chỉ có ở FAKE UI |
| | Bấm đúp hay mất câu trả lời xác nhận vẫn chỉ một nhóm | E1(f); test route 4 POST đồng thời | E2E + NỐI THẬT | Đủ |
| | ITEMS_EXIST đề nghị tải các tập còn lại; tất cả đã có thì không gửi lại; chế độ "all" trên danh sách chưa đủ cần xác nhận phạm vi | E5 (lượt 2 và 3); `test_all_on_an_incomplete_list_needs_the_seen_episodes_confirmed` | E2E + NỐI THẬT | Đủ; điều chặn của nút chỉ có ở FAKE UI |
| | Xác nhận là một transaction (lỗi ở thành viên, ở UPDATE EXPANDED hay ở DELETE preview đều rollback hết); quá 500 tập bị từ chối trước khi tạo | G (`CreateGroupTest`, 3 điểm lỗi), Q1 | ĐƠN VỊ + NỐI THẬT | Đủ |
| Hàng đợi | Nhóm >20 và nhóm 500 tập: giữ đủ thành viên, tối đa 100 tác vụ chưa xong (đếm bằng trigger trong transaction), điền theo thứ tự | Q1 | NỐI THẬT | Đủ |
| | Chỉ lấy vé khi có slot; lượt trình duyệt của một nguồn chạy nối tiếp, không giữ khóa worker hay store; slot mặc định 2, tối đa 3 | E1, Q1, C1r; test runs | E2E + NỐI THẬT | Đủ |
| | Không vòng lặp WAITING_LOGIN; chỉ bằng chứng đăng xuất thật mới cần đăng nhập; vé hết hạn được làm mới có giới hạn; lỗi mạng, đĩa, máy chủ giữ mã riêng | E4, C1t, C1e, Q8 | E2E + NỐI THẬT | Đủ |
| File | Tên `NNN - phim - mã` theo thứ tự nhóm dù tập xong lệch thứ tự; không ghi đè; giữ MKV; không để lại thư mục tạm của tập | E1(g), Q1; `MkvQueueTest` | E2E + NỐI THẬT | Đủ |
| | Kiểm âm thanh và hình trước khi publish; không tự quét | E1, C1a (F11); `assert_no_scan_and_no_yt_dlp` | E2E (phía bộ tải) | Một phần: watcher không chạy trong harness (xem Giới hạn) |
| | Một tập lỗi không làm hỏng nhóm | Q3 | NỐI THẬT | Đủ; trạng thái "Cần xử lý" chỉ có ở FAKE UI |
| | Đổi bản hay đổi validator không bao giờ nối byte; không có validator thì tải lại có giới hạn; câu trả lời 200 cả file cắt lên xuống thất thường vẫn dừng; file đổi bản qua 200 vẫn tải xong với số lần thử mới; tải nối mà nhận 2xx khác 200/206 thì dừng BAD_RESPONSE | T (`StrictVersionTest`, `StrictRefreshTest`, `WholeFileAnswerTest`) | ĐƠN VỊ | Đủ (giới hạn của 200 ở Giới hạn) |
| Nhóm | Dừng, tiếp, thử lại, hủy; ý định bền qua crash và restart; nhóm đã hủy không chạy lại khi đăng nhập muộn | Q2, E2; test intents; `EpisodeQueueTest` | NỐI THẬT + E2E | Đủ |
| | Liên kết cha EXPANDED và con đúng; xóa nhóm đúng phạm vi, giữ file đã publish, từ chối khi còn thành viên PENDING/HELD; dừng service không đóng store khi còn dùng | E1(h), Q3, Q7; `test_a_group_whose_members_still_wait_is_never_removed_even_when_every_task_is_final` | E2E + NỐI THẬT | Đủ |
| Bí mật | Không cookie, storage_state, vé, link ký hay đường dẫn kho phiên trong API (PC, điện thoại), sự kiện, log, DB, temp, header hay console | `secret_scan` của E1–E7 và C1; `CanaryTest`, `SecretTest` | E2E + ĐƠN VỊ | Đủ |
| | Mồi HTML của nguồn hiện thành chữ | E1, E2, E7 (tên phim, nhãn bản, dòng tác vụ, hộp chọn tập, thẻ nhóm, thành viên; không `<img>`, không vi phạm CSP); VDA | E2E + FAKE UI | Đủ; câu báo lỗi chứa mồi chỉ có ở FAKE UI |
| Mục 9.10 | Dán vào ô có sẵn; nguồn theo host chính xác; cửa sổ chỉ mở khi người dùng bấm, không mở theo từng tập | E1, E4; test config | E2E + ĐƠN VỊ | Đủ |
| | Chọn tất cả, Bỏ chọn tất cả và "Tải tất cả" từ trang thật | E7; VDA (3 test) | E2E + FAKE UI | Đủ |
| | Tập đăng thêm sau không tự vào nhóm đã xác nhận | `test_episodes_the_page_lists_later_never_join_a_confirmed_group` | NỐI THẬT | Đủ |
| | Không có tạm dừng toàn cục | `RouteInventoryTest`; VDA "no global pause" | ĐƠN VỊ + FAKE UI | Đủ |
| | API chỉ nhận id; id lạ bị từ chối | test route (BAD_SELECTION) | NỐI THẬT | Đủ |

**Sửa production** (mỗi chỗ có test FAIL trên code cũ trước khi sửa):
1. `download_media_file.py`, làm mới vé tính vào giới hạn tải lại do đổi bản (R67).
   - Lỗi: ở đường nghiêm ngặt, chỉ phản hồi 206 của phiên bản khác mới được đếm. Làm mới vé mà link mới có validator khác thì code bỏ phần đã tải và tải lại từ 0 mà không đếm. Một host đổi ETag theo vé, với vé ngắn hơn thời gian tải, sẽ tải lại mãi, mỗi vòng thêm một lượt Edge ẩn.
   - Bằng chứng: `StrictRefreshTest::test_a_new_version_after_every_refresh_still_ends_source_changed` trên code cũ ra `UNAVAILABLE` sau 9 lần làm mới, và chỉ dừng nhờ mã 410 của fixture.
   - Sửa: dùng chung bộ đếm với 206; vượt `MAX_VERSION_RESTARTS` thì SOURCE_CHANGED.
2. `download_media_file.py`, giới hạn thử lại bị lách. Lỗi này có sẵn ở `main`, trên cả đường thường.
   - Lỗi: một lần thử lỗi được coi là "có tiến triển" (đếm lại từ 0) khi phần tải dài hơn lúc bắt đầu lần thử đó. Máy chủ trả 200 cả file cho yêu cầu Range thì phần tải bắt đầu lại từ byte 0. Nếu điểm cắt lúc thấp lúc cao (3/4 rồi 1/2 của file), bộ đếm đi 1, 2, 1, 2 và lượt tải không bao giờ dừng.
   - Bằng chứng: `WholeFileAnswerTest::test_whole_files_that_fall_back_in_turn_end_network_once_the_tries_are_used` và `test_a_refresh_between_falling_cuts_does_not_start_the_tries_again`. Cả 6 trường hợp con trên code cũ ra `UNAVAILABLE` ở giới hạn 410 của fixture.
   - Sửa: lần thử chỉ tính là tiến triển khi phần tải dài hơn phần dài nhất đã giữ trong lượt đó (bắt đầu từ phần lượt nối tiếp; phần nghiêm ngặt không validator không bao giờ được giữ). Nay lượt dừng NETWORK sau 1 + `FILE_RETRIES` yêu cầu.
   - Kiểm chứng đối kháng tìm ra hồi quy của chính bản sửa này (MEDIUM). Khi file thật sự đổi bản và máy chủ trả 200 cả file của bản mới cho yêu cầu Range, mốc vẫn giữ độ dài của bản cũ. Các 206 nối thật của bản mới bị tính là lần thử hỏng: lượt dừng NETWORK, hoặc FORBIDDEN (không tiếp được) với link ký hết hạn, trong khi code của `main` tải xong.
   - Sửa tiếp: mốc về 0 khi phần bị bỏ vì khác bản: 206 hay làm mới vé khác bản (đã giới hạn bởi `MAX_VERSION_RESTARTS`), và 200 mà dấu phiên bản (`other_version`) cho thấy khác bản của phần đã tải, tối đa `MAX_NEW_VERSION_RESETS = 1` lần mỗi lượt, ở mọi đường, không tính vào SOURCE_CHANGED. 200 cùng bản hay không dấu vẫn giữ mốc, nên máy chủ đổi ETag xen kẽ vẫn dừng.
   - Bằng chứng của phần sửa tiếp, đều FAIL trước khi sửa: `test_a_whole_file_of_another_version_is_downloaded_again_with_a_fresh_count` (cũ: NETWORK) và `test_a_refreshed_link_that_brings_another_version_starts_the_count_again` (cũ: FORBIDDEN), mỗi test cả đường nghiêm ngặt và đường thường. Ba test mới ghim phần còn lại của quy tắc: mốc về 0 sau 206 khác bản, phần nghiêm ngặt không validator không đếm lại dù điểm cắt tăng, mốc bắt đầu từ phần lượt nối tiếp. Probe P1, P2, P3 của người kiểm chứng tải xong với đúng số yêu cầu như code của `main` (18, 10 và 6).
   - Kiểm chứng đối kháng vòng 2 (một LOW): đường thường giữ validator của lần thăm dò dù byte của phần đến từ một câu trả lời có ETag khác (máy chủ nhiều node, mỗi node một ETag). If-Range khi đó không khớp, 200 tải lại từ đầu và có thể dùng mất lần đặt lại mốc duy nhất của lượt cho các byte không hề đổi. Nay ở mọi đường, validator của phần là của câu trả lời bắt đầu phần ở byte 0 (không có dấu thì giữ của lần thăm dò). Bằng chứng: `StrictVersionTest::test_a_part_continues_with_the_version_of_the_answer_its_bytes_came_from`, đường thường FAIL trước khi sửa (If-Range là ETag của lần thăm dò).
   - Không đổi: 401 test của 12 module đường tải file đạt (transfer, sources, hls, http, https, page_sources, embedded_source, dispatch, provider_worker, account_sources, account_http, account_queue).
3. `download_api.DownloadService._waiting_logins`: số "lượt chờ đăng nhập" của khung tài khoản đếm cả tác vụ của tài khoản Windows khác, mà phiên của tài khoản này không đánh thức được. Nay chỉ đếm tác vụ có `account_owner` là SID của manager. Bằng chứng: Q5 FAIL với `{'alpha': 1, 'beta': 1} != {'alpha': 0, 'beta': 1}`.
4. `download_worker.remove` và `download_account_tasks._remove_group`, thư mục tạm của trang phim.
   - Lỗi: khi xóa nhóm, hay xóa trang đã tách mà nhóm đã mất, thư mục tạm `temp\downloads\<id trang>` do bước thăm dò tạo bị bỏ lại.
   - Sửa: cả hai đường đều xóa thư mục này. File còn bị giữ thì từ chối và giữ dòng DB. Không đụng `input\` hay thư mục của tác vụ khác.
   - Bằng chứng: Q3 và `test_a_page_whose_group_is_already_gone_can_still_be_removed` FAIL.
5. `download_groups.drop_left_previews`, mất danh sách tập do race.
   - Lỗi: bước thăm dò lưu danh sách tập khi tác vụ còn PROBING, rồi mới chuyển sang NEEDS_CHOICE. Lượt dispatch nào chạy xen giữa (phần dọn của nó xóa danh sách của trang "không còn chờ chọn") sẽ xóa mất danh sách. Trang khi đó chờ ở NEEDS_CHOICE mà Chọn tập trả 409 NOT_WAITING mãi mãi.
   - Đã gặp một lần dưới tải, ở dạng `KeyError 'fingerprint'` của Q6.
   - Bằng chứng: `test_a_dispatch_pass_between_storing_the_list_and_the_choice_keeps_the_list` trên code cũ cho danh sách là None.
   - Sửa: không xóa danh sách của trang còn PROBING.
6. `dashboard_v2\download-episodes.js`:
   - Lỗi: "Tải các tập còn lại" (sau ITEMS_EXIST) bỏ qua `blocker()`. Ô xác nhận phạm vi chưa tick vẫn gửi `confirm_scope: false` (server vẫn từ chối).
   - Sửa: nút giữ mọi kiểm tra, bị tắt kèm lý do khi có điều chặn, và `confirm()` kiểm lại trước khi gửi.
   - Nhãn bản bị lặp chữ: tách nhãn theo ` · ` trước khi bỏ trùng.
7. `download_media_file._receive`, câu trả lời 2xx khác khi tải nối. Lỗi này có sẵn ở `main`, trên cả đường thường; kiểm chứng đối kháng của lỗi 2 tìm ra.
   - Lỗi: khi phần tải đã có byte, một câu trả lời 2xx không phải 200 hay 206 (ví dụ 203 của proxy sửa câu trả lời, không có Content-Length) bỏ qua cả nhánh tải lại từ 0 lẫn kiểm Content-Range, nên thân của nó (file từ byte 0) bị nối vào cuối phần tải. Biết dung lượng thì một lượt sau có thể báo xong với file hỏng, cả ở đường nghiêm ngặt; không biết dung lượng thì phần tải lớn dần tới TOO_LARGE.
   - Sửa: khi tải nối chỉ nhận 200 (tải lại từ 0) hoặc 206 đúng đoạn; mã khác dừng `BAD_RESPONSE` ngay, không thử lại, không ghi byte nào.
   - Bằng chứng: `StrictVersionTest::test_a_range_answered_with_another_2xx_is_never_appended` (cả hai đường) trên code cũ ra SIZE_MISMATCH sau khi đã ghi byte lạ vào phần tải. Fixture có thêm tùy chọn `Reply.length=False` (không gửi Content-Length).

**Test** (thêm sau M5: 51 test Python và 12 test gate):
- E1–E7, `RouteInventoryTest`, C1a, C1t, C1e, C1r.
- Q1–Q8 và 6 test route hay crossing mới:
  - STALE_PREVIEW qua route;
  - xóa nhóm còn thành viên chờ;
  - tập đăng thêm sau;
  - "all" trên danh sách chưa đủ;
  - điện thoại hủy hay ngắt khi PC đang đăng nhập;
  - race danh sách tập.
- T1, `WholeFileAnswerTest` (gồm 6 test thêm sau kiểm chứng đối kháng; test máy chủ xen kẽ ETag nay chạy cả đường thường), test 2xx khác khi tải nối (có và không có Content-Length) và test validator của phần.
- G: rollback ở 3 điểm lỗi.
- Test runs: chờ khóa trình duyệt của nguồn.
- Ghim giá trị của kế hoạch: `TICKET_ATTEMPTS` 2, `MAX_VERSION_RESTARTS` 1, `LOCK_WAIT_SECONDS` 900, `HIDDEN_RUN_SECONDS` 180 (tối đa 600), `LOGIN_RECHECK_SECONDS` 30, `FILE_RETRIES` 5. Kế hoạch chỉ ghi "có giới hạn", nên đây là giá trị của code.
- Gate tài khoản 42 lên 54, gồm:
  - STALE_PREVIEW;
  - điện thoại vẫn dùng Chọn tập;
  - chặn "Tải các tập còn lại";
  - ITEMS_EXIST của fake;
  - nhãn bản;
  - Chọn tất cả/Bỏ chọn tất cả;
  - không có tạm dừng toàn cục (ghim mọi thao tác trên trang ở 4 chế độ);
  - escape ở nhãn, lỗi, xác nhận và thành viên.
- Wrapper Python của gate nay đòi đúng số test khai báo (22 và 54), không còn `>=`.
- Test cũ được làm chặt hơn, không test nào bị nới:
  - TTL ghim 3600 với mốc 3599/3600;
  - mã LOGIN_BUSY và LOGIN_CANCELLED;
  - quét bí mật toàn root;
  - lifecycle dùng service thật;
  - registry ở mức worker;
  - từng thành viên có đúng bản đã chọn;
  - thứ tự xong theo sự kiện;
  - đếm trần 100 bằng trigger.
- Fake frontend (`download-fake-accounts.cjs`) theo backend:
  - tiền tố thứ tự ít nhất 3 chữ số, `.mkv`, mã `S01E02`/`SP01`;
  - ITEMS_EXIST như `download_groups._new_items`.

  Khác biệt còn lại nằm trong comment: fake làm đầy nhóm khi GET, và `finishLogin` không kiểm generation hay SID.
- `verify-download.cjs` thoát 1 khi một test treo.
- Race của test M6, lộ ra khi C1e chạy cùng lúc với kiểm chứng đối kháng: worker ghi sự kiện ngay sau trạng thái (hai lệnh riêng), nên đọc sự kiện ngay khi thấy trạng thái có thể chưa thấy nó. Bốn chỗ như vậy (ba ở C1e, một ở E4) nay chờ sự kiện bằng `wait_event` của harness. Kiểm ngược: ghi mọi sự kiện chậm 1 giây (chỉ trong bộ nhớ), C1e và E4 vẫn đạt. Production không đổi.

**Review**
- Bốn review chỉ đọc sau đợt test đầu:
  - sửa lỗi production: không có lỗi;
  - bảo mật: không CRITICAL hay HIGH; 2 MEDIUM (giá trị cookie trong `results.json`, danh sách bí mật cố định) và 6 LOW;
  - chất lượng test: 1 HIGH (hạn thời gian tiêm vào chặt hơn mức chờ của test) và 7 MEDIUM;
  - ma trận: 7 mục một phần, 3 mục chưa có test.
- Một workflow 4 nhóm file tách biệt đã sửa các điểm đó. Mỗi nhóm có người kiểm chứng đối kháng; 3 nhóm qua sau một vòng sửa.
- Workflow đó tìm ra lỗi 2 ở trên. Lỗi 5 lộ ra từ một lần test chập chờn dưới tải.
- Hai bản sửa production cuối được kiểm chứng đối kháng riêng, hai vòng. Vòng 1 (8 agent, judge cuối): bản sửa race đạt; bản sửa giới hạn thử lại giữ được điểm dừng (kiểm mô hình vét cạn 18 cấu hình, không chu trình) nhưng gây hồi quy MEDIUM (200 của bản mới, lỗi 2) và lộ lỗi 2xx khác (MEDIUM, có từ trước, lỗi 7), cùng hai điểm LOW về test và docstring; cả bốn đã sửa. Vòng 2 (ba hướng: hồi quy, điểm dừng, test và tài liệu): điểm dừng không bác được (kiểm mô hình với khóa trạng thái có thêm `new_versions`: 18/18 cấu hình không chu trình); hướng hồi quy có hai LOW (validator của lần thăm dò ở đường thường, đã sửa ở lỗi 2; lần đổi bản thứ hai trong một lượt không được đặt lại mốc, ghi ở Giới hạn) và một INFO đã biết. Đột biến trên module transfer: 21 đột biến của dòng code mới, ban đầu 13 bị test bắt; sau các test bổ sung 19 bị bắt. Hai đột biến còn sống, ghi ở Giới hạn: bỏ Last-Modified khi xét 200 khác bản, và dùng chung bộ đếm với lần đổi bản qua 206 (chỉ khác khi file đổi bản hai lần trong một lượt). Bước judge của vòng 2 bị dừng để tiết kiệm thời gian sau khi đã có đủ kết quả của ba hướng; các tiến trình và thư mục tạm của nó đã được dọn.
- Không sửa (ghi ở Giới hạn): registry production là dict có thể sửa được (LOW, chỉ trong tiến trình test), mẫu `.gitignore` theo tên chính xác (INFO), tên site thật trong tài liệu cũ của `main` (INFO, có từ trước).

**Kết quả trên code cuối**
- E2E và crossing (`BILIFLOW_REQUIRE_E2E=1`): 12 test, 12 đạt, 0 skip, 0 lỗi (558 giây; chạy lại sau các sửa cuối: 12 đạt, 0 skip, 594 giây).
- Gate node: `verify` 38/0, `verify-adapter` 37/0, `verify-download` 22/0, `verify-download-accounts` 54/0, `verify-review` 32/0.
- Script độc lập của Codex `download_account_m5_mode_review.py --expect-pass`, chạy trên bản sao giống từng byte (SHA-256 `cfbdb69cc2b4…`): đạt; normal, 503 và 404 đều không có nút tài khoản.
- Test tập trung của các module đã sửa: hàng đợi, nhóm, ý định, API, worker, robustness 147 đạt; 12 module của đường tải file (transfer, sources, hls, http, https, page_sources, embedded_source, dispatch, provider_worker, account_sources, account_http, account_queue) 402 đạt sau các sửa cuối, transfer riêng 29 đạt; dashboard 24 đạt; sources (Edge thật) 37 đạt; sources, login và browser 139 đạt. Không skip.
- Full suite trên code cuối (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree): 2.433 test trong 1.792,2 giây: 2.407 đạt + 26 skip, 0 FAILURE, 0 ERROR (log `m6-full-2.log` trong scratchpad của phiên; lần chạy trước các sửa cuối: 2.425 test, 2.399 đạt + 26 skip, 0 FAILURE/ERROR). 26 skip có lý do, giống các lần trước: 11 thiếu FFmpeg của project trong worktree, 5 thiếu preview ident Golden, 4 thiếu bộ nhớ logo studio của project, 1 thùng rác thật (opt-in), 1 không có DB Control Center thử, 1 nhãn v1 không ở revision 469, 1 thiếu bộ phân loại an toàn đã cài, 1 thiếu Playwright của node cho kiểm trình duyệt điện thoại của trang review, 1 thiếu dữ liệu phim thật. Không skip nào thuộc test tải hay tài khoản; các module E2E chạy thật.
- Ảnh và `results.json`: trong scratchpad của phiên, `m6\wave3\A-e2e\shots\` (25 PNG: PC sáng/tối, điện thoại 390 sáng và 375 tối, chế độ chưa rõ, đăng nhập lỗi, hết phiên, "Tải tất cả" với mồi HTML). `results.json` không chứa bí mật.
- Dọn dẹp: không còn thư mục `m6-*` hay `session-browser-*` nào của test, không còn tiến trình Edge hay Python nào của test.
- Không cài gì, không đổi dependency hay license nên không chạy license audit. Tracked files chỉ có host `.example`, 127.0.0.1 và literal địa chỉ công khai của fixture. `.gitignore` vẫn bỏ qua `config/download_accounts.local.json` và `state/` (kho phiên).

**Giới hạn của M6**
- Chưa có nguồn thật: `PAGE_READERS` và `LOGIN_VERIFIERS` vẫn rỗng. M6 chỉ chứng minh cấu trúc trang của fixture (`FixtureReader`/`FixtureVerifier` trên host `.example`). Đọc trang thật, bằng chứng đăng nhập thật, host vé và file thật, giới hạn tốc độ và thời hạn phiên thật của site là việc của M7. Không được coi nguồn thật nào là đã hỗ trợ.
- Không có cửa sổ hiện: bản thay thế headless bỏ HeadedPermit. Cửa sổ Edge có giao diện, việc người dùng gõ, thử thách trong cửa sổ và việc đóng bằng nút X là kiểm tra của người dùng trên instance thử (M7).
- Không có tài khoản Windows thứ hai thật: cách ly giữa tài khoản dùng SID, protector và ACL giả. DPAPI và ACL thật chỉ của tài khoản chạy test (mốc trước, không chạy lại riêng).
- Mạng được tiêm: DNS thật, IPv6, proxy/PAC, CDN, chuỗi chứng chỉ thật và lưu lượng nền của Edge trên mạng thật không được kiểm.
- Điện thoại: chỉ trên 127.0.0.1, với Edge headless ở 390 và 375. Wi-Fi hay Tailscale thật, `tailscale whois` và trình duyệt điện thoại thật không được kiểm.
- Không tự quét: watcher và scheduler không chạy trong center của E2E; test chỉ kiểm bộ tải không xếp lệnh quét nào. Chuỗi bộ tải → `input\` → watcher → không quét chưa có test tự động. Phía watcher có các test riêng:
  - `test_scheduler.InputWatcherRestoreTests::test_different_bytes_at_the_old_path_are_rejected_once_and_become_a_new_job`;
  - `test_scheduler::test_start_job_queues_only_a_video_waiting_for_setup`;
  - lần chạy thật D4.
- Dung lượng: dự trữ đĩa được bỏ qua bằng disk probe tiêm. Đường chờ dung lượng của tài khoản do Q8 kiểm.
- Quy mô: nhóm 500 tập dùng `QueueProvider` (không trình duyệt) và một thân MKV nhỏ. Chi phí thật ở hàng trăm tập (hai lượt Edge ẩn mỗi tập: thăm dò và lấy nguồn mới) và giới hạn vé thật chưa được đo.
- Không chạy dưới tải CPU nhân tạo (prompt cấm). Harness đã dùng hạn của production. Giữ vé 25 giây trong C1r vẫn bị giới hạn bởi hạn 30 giây mỗi yêu cầu của trình duyệt phiên.
- Câu trả lời 200 cả file ở đường nghiêm ngặt (máy chủ không khớp If-Range) không tính vào `MAX_VERSION_RESTARTS`, đúng như mục 9.13: phần tải luôn bắt đầu lại từ 0 với bản của chính câu trả lời đó, nên không bao giờ trộn byte. 200 cùng bản hay không dấu: lượt dừng NETWORK theo `FILE_RETRIES`, hoặc tải xong khi điểm cắt cứ tăng dần. 200 khác bản chỉ đưa mốc tiến triển về 0, một lần mỗi lượt: file đổi bản lần thứ hai trong cùng lượt thì các lần thử sau không được đếm lại, lượt dừng NETWORK và người dùng bấm Tiếp tục để chạy lượt mới. Muốn coi 200 khác bản là đổi bản (SOURCE_CHANGED) thì cần quyết định riêng.
- Host đổi ETag theo từng vé: file cần hơn hai vé sẽ kết thúc SOURCE_CHANGED sau một lần tải lại, thay vì lặp. Đúng quy tắc "không chứng minh được cùng bản thì không nối"; người dùng bấm Thử lại để chạy lượt mới.
- Ngữ nghĩa R17: khi đã có một phiên cũ còn hiệu lực, lần đăng nhập thất bại, bị hủy hay hết giờ vẫn để CONNECTED (của phiên cũ) kèm `error_code` của lần lỗi. "Không bao giờ CONNECTED" áp dụng cho lần thử lỗi, không cho nguồn.
- `last_login` chỉ ở bộ nhớ. Sau restart, lỗi trang hay lỗi trình duyệt chỉ còn `LOGIN_FAILED` chung của manager.
- Hiển thị:
  - nhãn "CONTROL CENTER" trên thanh trên là chữ tĩnh ở mọi chế độ; chỉ báo thiết bị thật là `#dl-mode`;
  - nhãn bản có chữ riêng (ví dụ `Bản 1080p`) vẫn kèm chất lượng (`Bản 1080p · 1080p`).
- Kiểm tra còn hẹp:
  - nút mùa `dl-ep-season` chưa có test;
  - 200 khác bản chỉ có Last-Modified (phần có validator Last-Modified) được `other_version` phủ ở bảng của nó, chưa có test đầu-cuối: fixture chỉ so If-Range với ETag (đột biến bỏ Last-Modified ở chỗ gọi còn sống);
  - đột biến dùng chung giới hạn của 200 khác bản với `other_versions` còn sống: nó chỉ khác khi một lượt nghiêm ngặt đã tải lại vì 206 khác bản rồi file lại đổi bản qua 200, tức lần đổi bản thứ hai trong lượt (xem giới hạn của câu trả lời 200);
  - dưới tải rất nặng (các lượt đột biến chạy song song với E2E), `test_download_account_sources.SessionTest.test_cookies_rotated_before_a_cancel_are_kept` (từ M3) FAIL 2 lần; trong các lần chạy module của M6 nó đạt;
  - ghim "không tạm dừng toàn cục" chỉ phủ một ảnh trang ở 4 chế độ;
  - gate gọi trực tiếp handler của hộp chọn tập.
- Phạm vi refactor:
  - `LOGIN_VERIFIERS` và `PAGE_READERS` là dict thường (nên đổi sang chỉ đọc khi M7 thêm bộ thật);
  - `.gitignore` bỏ qua theo tên file chính xác;
  - vệ sinh temp của các test tải cũ (`remove_tree`, `TemporaryDirectory`) không refactor;
  - `tests/account_e2e_fixtures.py` (1.145 dòng) và `tests/test_download_account_queue.py` vượt mức mềm 800 dòng; cả hai là file test.
- Kiểm Edge của `test_download_account_browser.py` và `test_download_embedded_source.py` chỉ xem `Program Files (x86)`. Harness M6 xem đủ các đường của kênh `msedge`.
- Các giới hạn có từ trước ở mục 9.18 ngoài phần tài khoản không được biến thành refactor trong M6:
  - GET `/api/phone-mode` cũ có thể đè POST Bật/Tắt mới hơn;
  - `app.js` dùng `state.remote`;
  - chữ của link "Dung lượng".
- Full suite dài thêm khoảng 10 phút vì E2E trên Edge thật.

**Cho M7** (chưa làm):
- M6 không chứng minh nguồn thật tải được. M7 cần:
  - bộ đọc trang và bộ xác nhận đăng nhập của nguồn thật;
  - cấu hình cục bộ bị Git bỏ qua (`config/download_accounts.local.json`);
  - người dùng tự đăng nhập trên một instance thử.
- Câu hỏi mới của người dùng về mốc 1 giờ: đây là ngưỡng dự phòng của ứng dụng, không phải thời hạn phiên thật của site. Nếu một nguồn có bằng chứng kiểm phiên đáng tin (ví dụ một trang chỉ trả lời khi đã đăng nhập), M7 đánh giá việc kiểm phiên trực tiếp. Bây giờ không đổi hành vi và không tăng TTL.
- Đăng nhập từ điện thoại hay qua remote desktop là phạm vi riêng, chưa triển khai.

Tên đủ của các test chính:
- E1 `DashboardFlowTest.test_pc_sign_in_wakes_the_page_and_picked_episodes_land_in_input`;
- E2 `…test_phone_views_accounts_but_drives_episodes_and_groups`;
- E3 `…test_unknown_mode_and_failed_sign_ins_never_post_or_connect`;
- E4 `DashboardFlowsTest.test_session_expiry_mid_group_waits_then_resumes_in_order_after_a_new_sign_in`;
- E5 `…test_an_incomplete_list_and_a_repeated_page_offer_only_what_is_new`;
- E6 `ProductionDefaultsTest.test_no_source_and_an_unsupported_source_show_guidance_and_never_fall_back`;
- E7 `DashboardFlowsTest.test_all_episodes_of_a_complete_series_make_one_group_and_html_bait_stays_text`;
- C1a `RealProviderQueueTest.test_films_go_straight_or_wait_for_a_variant`;
- C1t `…test_an_expired_ticket_or_a_refused_file_is_asked_for_again_never_a_sign_in`;
- C1e `…test_page_server_network_and_gone_errors_keep_their_codes_and_only_sign_out_evidence_waits`;
- C1r `…test_runs_of_one_source_take_turns_and_never_hold_the_worker_or_the_store`;
- Q1 `BigGroupTest.test_a_500_episode_group_keeps_every_member_and_fills_under_the_cap_in_order`;
- Q2 `RouteTest.test_group_routes_reach_running_waiting_interrupted_and_expired_episodes`;
- Q3 `RouteTest.test_one_failed_episode_leaves_the_others_done_and_removal_keeps_their_files`;
- Q4 `RouteTest.test_the_phone_listener_refuses_every_account_route_before_reading_a_body`;
- Q5 `WaitingLoginTest.test_another_accounts_task_is_not_counted_here_and_wakes_only_under_its_own_account`;
- Q6 `ServiceCrossingTest.test_a_restart_keeps_a_valid_session_and_settles_an_open_sign_in_without_a_window`;
- Q7 `ServiceCrossingTest.test_stop_waits_for_a_real_sign_in_thread_and_a_running_task_then_routes_answer_503`;
- Q8 `TransferTest.test_a_full_disk_waits_for_space_and_never_for_a_sign_in`.

### 9.20 M7 (2026-10-09 – 2026-10-10, đóng trong worktree, chờ Codex review): adapter "release-forms" cho nguồn thật thứ nhất

**Bằng chứng thật của M7 đã đủ; reader production đã bật (2026-10-10, người dùng đồng ý sau review của Codex; mục "Bật reader và đóng M7" ở cuối).** Đã nghiệm thu thật (2026-10-10):
- đăng nhập trong cửa sổ BiliFlow;
- đọc danh sách bằng phiên BiliFlow;
- cổng quảng cáo với ngoại lệ A (mục Final-verify bên dưới);
- buổi chiều: một vé qua phiên BiliFlow (1 POST) và probe chính vé đó (1 MiB, 206, ETag mạnh, MKV HEVC/AAC) (mục "Quan sát một vé" bên dưới).

Còn chờ: Codex review trước khi đưa vào bản chính; giữ ngoại lệ A và đoạn Network khi merge/deploy là quyết định riêng. Tên nguồn, host, URL đăng nhập, tên phim và bằng chứng thô chỉ ở biên bản riêng ngoài repo (Git-ignored); mục này chỉ mô tả cấu trúc chung.

**Đã khảo sát (người dùng xác nhận từng bước; nguồn thật):**
- Trang phim render sẵn ở server: id phim ở `[data-movie-id]`, vùng tải `#download-files`; phim bộ có các mùa `details.cd-season` (gập sẵn) với nhãn "Mùa N", mỗi file là một thẻ `article.cd-release` với tên "Tập N · <tên file>" và meta (chất lượng, âm thanh, codec, dung lượng chữ); phim lẻ là `.cd-release-list` không mùa. Nguồn không có mã tập riêng; mã file nằm trong form tải và không theo thứ tự tập.
- Chưa đăng nhập: mỗi thẻ có link tới trang đăng nhập. Đã đăng nhập: mỗi thẻ có một form POST thường (`target=_blank`, không script nào bắt) tới `/download-links/<mã file>/access`.
- Trang đăng nhập: form POST cùng host, không CAPTCHA, không cổng quảng cáo; trong trình duyệt phiên của BiliFlow chỉ cần host portal để hiện và gửi form. Sau khi gửi, về trang chủ.
- Bằng chứng đăng nhập: `/ajax/notifications` trả 200 JSON đúng hai khóa khi đã đăng nhập; chưa đăng nhập thì chuyển hướng về trang đăng nhập (đã đối chứng bằng chính `LoginView.fetch` của BiliFlow: REDIRECT_REFUSED, fetch None).
- Trang phim có cổng chặn quảng cáo phía client: một script quảng cáo bên ngoài phải tải được, nếu không một `dialog` modal khóa trang. Trong trình duyệt phiên của BiliFlow cổng mở và Playwright `click(trial=True)` trên nút của thẻ hết thời gian vì dialog chặn chuột (thử trên trang chưa đăng nhập; với phiên của BiliFlow chưa thử được).
- Trang vé (2026-10-09 ~20:45, một vé): Browser pane chặn tab mới do agent bấm (form `target=_blank`; không có POST vé). Theo lựa chọn của người dùng, agent bấm đúng một lần trong Chrome cá nhân qua extension, sau khi người dùng tự đăng nhập ở đó; chỉ đọc DOM, không bấm nút tải, không reload, không đọc cookie, không đưa gì vào BiliFlow. Cấu trúc chung: trang vé ở một host vé riêng (khác portal, có phiên riêng), đường dẫn chứa token; link tải là `a#downloadBtn` với `href` có sẵn trong HTML tới một host file thứ ba cùng site, đường dẫn `<token>/download`, không query; `data-state` `loading` → `success` (`blocked`/`error` khi cổng quảng cáo của trang vé hoặc lỗi); đếm ngược và cổng chặn quảng cáo riêng chỉ khóa cú bấm phía client. Trang ghi: link chưa dùng hết hạn sau 1 giờ; sau khi bắt đầu tải, tải tiếp được trong 12 giờ trên cùng mạng/IP; hỗ trợ resume. Cú bấm đầu vào summary của mùa bị script popunder nuốt (cả pane và Chrome). Chưa biết: host file có cần cookie/Referer không, Range/validator thực tế (chỉ xác định bằng probe ≤ 1 MiB trong BiliFlow).

**Quyết định của Codex (design review M7, ngoài repo), triển khai thử trong worktree:** (1) ngoại lệ tài nguyên A: một script exact URL, vai trò riêng, chỉ ở lượt ẩn; (2) khóa tập `s<mùa>:e<tập>` từ hai số có nhãn của trang, variant = mã file; (3) runtime mở đúng mùa đang gập trước một lần submit; (4) verifier = trang portal ngoài trang đăng nhập + notifications 200 JSON đúng schema; TTL 3.600 giây giữ nguyên, 419 không thành WAITING_LOGIN. Việc chọn A là quyết định thử của Codex, không phải người dùng đã chấp nhận rủi ro script quảng cáo chạy trong trang có phiên.

**Đã code (fixture, chưa nghiệm thu thật):**
- `download_account_release_forms.py`: `FilmPageReader` (id phim, tiêu đề, mùa/tập từ nhãn "Mùa N"/"Tập N ·", không đọc số trong tên file; thẻ thiếu số, form hay mã file thành mục không đọc được và danh sách chưa đầy đủ; thẻ ngoài `#download-files` không đọc; `signed_out` chỉ khi có link đăng nhập trong thẻ và không có form tải) và `NotificationsVerifier`. `reads_tickets = False` lúc đó (bật ngày 2026-10-10, mục "Bật reader và đóng M7"): trang vé chưa được đọc nên `get_ticket` từ chối trước mọi cú bấm (TICKET_UNSUPPORTED), không tốn vé.
- Registry chỉ đọc (`MappingProxyType`): `ACCOUNT_ADAPTERS` thêm `release-forms` (ttl); `PAGE_READERS` và `LOGIN_VERIFIERS` có đúng `release-forms`; `ticket-files` vẫn không có reader/verifier nên vẫn bị từ chối trước browser và yt-dlp.
- `RawEntry.reveal` (selector của đúng `details` chứa form, dựng bằng `:has(form[action$=…])`), `navigation` thành `(page_url, trigger, reveal)`. `get_ticket`: reader không đọc được vé thì từ chối; mùa gập thì bấm đúng `summary` của nó (mùa đã mở thì không bấm), chờ mở có giới hạn, đọc lại trang và đòi đúng một mục cùng trigger/reveal (TICKET_TARGET_CHANGED nếu khác); trigger phải khớp đúng một phần tử (TICKET_TARGET_UNCLEAR, không bấm "phần tử đầu"); kiểm hủy/thời hạn giữa các bước; bấm một lần, không force, không script, không đóng dialog. Đọc lại trang trong `_ticket` đòi đúng một mục.
- `LoginView.left_sign_in()`: trang đang hiện là https của host portal và không phải trang đăng nhập của nguồn. `download_account_pages.is_sign_in_page` (đổi tên từ `_is_sign_in`).
- Test: `tests/test_download_account_release_forms.py` (27 test: schema notifications, verifier, `left_sign_in`, registry chỉ đọc, khóa tập/bản/thứ tự/xung đột/fingerprint, đọc mùa gập theo số, phim lẻ, thẻ không đọc được, trang chưa đăng nhập, reader production không tốn vé, mở mùa và đúng một POST, mùa đã mở không bấm lại, một file trong hai mùa không bấm, một form hiện hai lần trên phim lẻ không bấm, thẻ đổi sau khi mở không bấm, modal chặn → CLICK_FAILED không POST, hủy và hết giờ giữa mở mùa và bấm) trên fixture tự làm `tests/release_forms_fixtures.py` (`.example`, mã/tên bịa). Test registry cũ đổi từ "rỗng" sang "chỉ release-forms", giữ assertion adapter không có reader/verifier bị từ chối. Test được viết sau code trong lượt này (không phải test đỏ trước); để bù, một lượt đột biến tay (script ngoài repo, file được trả lại nguyên byte) bỏ từng chốt của `get_ticket` (kiểm mùa đã mở, đòi đúng một trigger, đọc lại sau khi mở, `reads_tickets`): 4/4 đột biến bị test bắt.
- Kết quả test (2026-10-09 ~21:00): toàn bộ `test_download_account*` với `BILIFLOW_REQUIRE_E2E=1`: 460 test, 1.461 s, 459 OK, 1 FAIL: E6 (`ProductionDefaultsTest`) còn đòi registry verifier rỗng (docstring đã sửa, assertion bị sót). Đã sửa assertion thành "chỉ release-forms, adapter của nguồn mẫu không có verifier"; chạy lại riêng: 1/1 OK. Chưa chạy lại full suite trong lượt này.

**Trước review (đã thay bằng phần dưới):** ngoại lệ A bị auto mode classifier của phiên từ chối ("[Security Weaken]") và không làm vòng; `ticket_page` chưa viết.

**Sửa sau review M7 của Codex (2026-10-09 tối; review và prompt FIX-AND-TICKET ngoài repo):**
- **P2, mã phim trước khi bấm.** `get_ticket(…, film_id=…)` luôn đọc lại trang ngay trước cú bấm: sau khi mở mùa, khi không có mùa, và khi `_ticket` quay lại trang sau phân trang. Id phim phải là phim đã chọn và phải có đúng một mục `file` cùng tập, mã file, trigger, reveal và `request`. Khác thì TICKET_TARGET_CHANGED (hoặc SourceChanged khi trang quay lại đã thành phim khác), không POST; mục hiện hai lần thì TICKET_TARGET_UNCLEAR. Cancel/thời hạn giữ nguyên; reader không đọc vé vẫn không xin vé. Script regression của Codex (`download_account_m7_review_tests.py`, sha256 `f2d9fb1a…a0cdcfb`, không sửa script hay kỳ vọng): trước khi sửa 2/2 FAIL, sau khi sửa 2/2 PASS (sha256 giống trước/sau). Test trong repo: phim đổi sau khi mở mùa, phim không mùa được đọc lại trước cú bấm (fixture release-forms), trang đọc lại phải còn là phim đã chọn (provider fixture, cả khi quay lại trang 1 của danh sách hai trang).
- **Nguồn gốc vé khi trang vé không hiện mã file.** `FilmPageReader.shows_ticket_ids = False`; mỗi mục có `request` (URL của form POST: https, ≤ 2.048 ký tự, riêng tư như mọi URL của lượt; mục thiếu nó không bao giờ nhận vé). `SessionBrowser` ghi các yêu cầu điều hướng của riêng lượt trong bộ nhớ (`NavigationStep`: số thứ tự, phương thức, URL bỏ fragment, frame, target của redirect, status hay mã từ chối; tối đa 200, vượt thì `navigations_dropped` và không vé nào được nhận). `_TicketFlow._answers_this_click` chỉ nhận một trang vé khi: đúng một yêu cầu điều hướng tới `request` của mục kể từ cú bấm; trang là popup do chính trang phim mở sau cú bấm (opener), yêu cầu đầu chưa có frame hoặc đúng frame đó; mỗi redirect được nối bằng lần điều hướng kế tiếp của frame tới đúng target (hop page đã kiểm); bước cuối 2xx ở đúng URL trang đang hiện; frame không điều hướng gì khác. Không dùng tên file, S02E02, dung lượng hay "popup đầu"; reader không giữ trạng thái (mọi thứ ở `_TicketFlow` của từng lần lấy vé). Hệ quả cần xem khi nghiệm thu: trang vé tự tải lại, hoặc đi nơi khác rồi quay lại, không được nhận (lỗi an toàn TICKET_NOT_OPENED, không dùng nhầm vé); trang vé thật bảo người dùng "tải lại trang" khi phát hiện chặn quảng cáo. Test logic không cần browser: `tests/test_download_account_provenance.py` (mỗi ca giữ chuỗi đúng và đổi đúng một điều: popup do trang khác mở, hai yêu cầu tới URL của mục, frame đi nơi khác trước target, redirect không được theo, đi rồi quay lại, bước cuối bị từ chối/404/302, URL đang hiện khác, trang có từ trước cú bấm, yêu cầu từ frame khác, bản ghi bị mất, mục không có `request`, chuỗi dài hơn MAX_HOPS).
- **`ticket_page`.** Cần đúng một `a#downloadBtn`. `data-state="blocked"` → thử thách (SOURCE_CHALLENGE, không gỡ cổng); `error` → TICKET_FAILED; sẵn sàng chỉ khi `success`, không còn `aria-disabled="true"` hay `pointer-events-none` và có href; mọi trạng thái khác (loading, ready, default, còn khóa) là chờ, hết giờ thì TICKET_TIMEOUT. Một href có sẵn trong HTML không bao giờ là vé sẵn sàng; không bấm nút tải, không rút ngắn đếm ngược, không giả script. Fixture: đếm ngược, kết thúc success/blocked/error/loading mãi, popup quảng cáo và trang vé của file khác mở trước, POST trả 302, trang trung gian bằng script, redirect ra ngoài nguồn, redirect thiếu Location, mục không có `request`.
- **Khoảng trống bằng chứng, vì vậy `reads_tickets` vẫn False (không bật bằng đoán):** script nội tuyến của trang vé thật (11,5 KB) chưa đọc hết nên chưa biết nghĩa thật của `ready` so với `success` và giá trị đếm ngược ban đầu; cổng quảng cáo riêng của trang vé (`requiresAdVerification`) chưa thử trong trình duyệt của BiliFlow (không có script quảng cáo ở đó, có thể luôn `blocked`); chưa biết POST tới trang vé là 302 hay trang trung gian (redirect cross-origin bị ẩn khỏi performance entries); chưa thấy trạng thái hết hạn hay đăng xuất của trang vé; trang vé có phần đăng nhập riêng của host vé nên chưa biết phiên ở host vé được lập qua chính luồng POST hay cần đăng nhập riêng trong cửa sổ BiliFlow. Nếu trang vé thật về `blocked` trong BiliFlow thì cần quyết định riêng; không gỡ cổng.
- **Vai trò host và phiên.** Portal, tickets, files là exact host riêng như trước. Cookie và storage origin của host vé chỉ vào vault qua `own_state`/`save_rotated` của chính lượt BiliFlow (test: cookie riêng của host vé được lưu cùng phiên và gửi lại ở lượt sau; cookie portal không sang host vé; host file không nhận cookie nào vì probe là `SafeHttp` không cookie). Không nhập gì từ Chrome hay pane. Chưa có bằng chứng host vé hay host file cần cookie. Config riêng (ngoài repo, `temp\m7-<nguồn>-private\download_accounts.local.json` của thư mục chính, chưa đặt vào instance nào): portal và tickets exact; `files` để trống vì host file trần chưa quan sát được (extension che hai nhãn), không đoán tiền tố, không wildcard; validator của BiliFlow: 0 problem.
- **Ngoại lệ A (làm trong chế độ quyền thủ công; người dùng duyệt từng thao tác sửa code; lần này không bị chặn).** Đây là phương án thử Codex chọn, không phải rủi ro người dùng đã chấp nhận.
  - Config `page_script`: exact https URL, cổng mặc định, có path, không `?`, `#`, `@`, không phải host nào của nguồn. Không thuộc `all_hosts`: không bị nhận là link của nguồn, không vào vault, không trong phạm vi của `LoginView.fetch` hay client của lượt ẩn.
  - `download_account_page_script.PageScript`; `SessionBrowser(page_script=…)` chỉ cho lượt ẩn (cửa sổ đăng nhập hoặc script trên host của nguồn → ValueError); `run_with_session(page_script=True)` chỉ ở lượt vé (`_session_run(ticket)`), không ở lượt đọc danh sách; `browser_options` không được tự đặt nó.
  - Mọi yêu cầu tới host script chỉ được trả lời khi đúng script: loại `script`, GET không body, https, đúng host/path, cổng mặc định, không user, query rỗng hoặc `_=<10–16 chữ số>`. Đi qua `SessionHttp` riêng của host đó (địa chỉ công khai, DNS ghim lúc kết nối, TLS, 20 s, ≤ 2 MiB, hủy), là GET của chính URL đã cấu hình (query `_=` của trang được kiểm nhưng không gửi đi: nếu gửi, script trong trang có thể nhét dữ liệu vào các chữ số đó, khoảng 26 byte mỗi lượt; sửa theo review chỉ đọc), chỉ gửi Accept và User-Agent (không cookie, Authorization, CSRF, Referer, Origin). Không theo redirect (REDIRECT_REFUSED, kể cả về chính URL đó); chỉ 200 có content-type JavaScript; trang chỉ nhận content-type và `no-store` (không Set-Cookie); tối đa 4 lần mỗi lượt (PAGE_SCRIPT_LIMIT). Mọi thứ khác trên host đó: PAGE_SCRIPT_TYPE/METHOD/URL.
  - Giới hạn còn lại: script chạy với quyền của trang phim (đọc được DOM và cookie không HttpOnly); đây không phải sandbox. Yêu cầu con của nó ra host khác bị chặn (HOST_NOT_ALLOWED).
  - Test: `tests/test_download_account_page_script.py` (đúng script không phiên; mọi thứ khác trên host đó; redirect; địa chỉ riêng, DNS rebinding, TLS sai; quá lớn, chậm, 404, không phải script; hủy; yêu cầu con; giới hạn mỗi lượt; không có A, cửa sổ đăng nhập, host của nguồn; chỉ lượt xin dùng; trang phim có cổng chỉ bấm được khi có A, fixture `gate_script`; handler không browser: chỉ Accept và User-Agent đi ra, trang chỉ nhận content-type và `no-store`), config `page_script` đúng/sai.
- **Đột biến tay** (script ngoài repo, file nguồn được trả lại nguyên byte, kiểm sha256): ngoại lệ A 10/10 bị test bắt (gửi header của trang, query bất kỳ, loại tài nguyên bất kỳ, bỏ giới hạn mỗi lượt, path bất kỳ, header trả lời vào trang, trả lời lỗi/không phải script vào trang, cửa sổ đăng nhập có A, mọi lượt có A, lượt vé không có A); mã phim và nguồn gốc vé 6/6 (bỏ kiểm mã phim, bỏ nguồn gốc, bỏ opener, nhận nhiều yêu cầu tới URL của mục, bỏ kiểm target của redirect, bỏ kiểm điều hướng khác của frame). Lượt đầu có 5 mutant sống sót: "header trả lời vào trang" (Edge tự chặn Set-Cookie cross-site mặc định Lax nên test cũ không thấy; đã thêm test handler và cookie `SameSite=None`) và bốn chốt nguồn gốc (các chốt khác che; đã thêm `test_download_account_provenance.py`); sau khi thêm test, cả năm bị bắt.
- Cookie của host vé: fixture `ticket_cookie` và test `test_the_ticket_hosts_own_cookie_stays_with_the_session_and_the_file_host_gets_none`.
- **Review chỉ đọc (một agent, sau khi code ổn định):** 0 CRITICAL, 0 HIGH. MEDIUM đã sửa: query `_=` không còn được gửi tới host script (trên). LOW, ghi lại, chưa sửa (đều làm từ chối nhầm theo hướng an toàn, không dùng nhầm vé): (1) target của redirect được so nguyên dạng với URL chuẩn hóa của trình duyệt, nên `Location` không có path, có `:443`, chữ hoa hay ký tự cần mã hóa sẽ làm hỏng chuỗi (TICKET_NOT_OPENED); (2) giới hạn 200 bước điều hướng tính cho cả lượt và không reset, nên một lượt quá nhiều điều hướng (kể cả iframe quảng cáo bị từ chối) không nhận được vé nào nữa; (3) `resolve` luôn là lượt có thể xin vé, nên trang phim bộ hay phim nhiều bản cũng nạp script dù kết thúc ở bước chọn (chỉ `discover` chắc chắn không có script). Ngoài code: invariant "Network" trong `AGENTS.md` của worktree chưa nhắc ngoại lệ A; trước khi merge cần người dùng chấp nhận rõ và sửa invariant (agent không tự sửa chính sách).

**Kết quả test sau review (2026-10-09/10):**
- **E6 trước và sau.** Lượt trước review: 460 test, 459 OK, 1 FAIL (E6 còn đòi registry verifier rỗng). Sau khi sửa assertion: E6 chạy riêng 1/1 OK. Lần này E6 chạy cùng code cuối trong bộ focused và trong full suite. Đây không phải con số "460/460".
- **Focused, 23:32–23:44.** release_forms, listing, login, sources, config, page_script, provenance, browser và E6 với `BILIFLOW_REQUIRE_E2E=1`: 252 test, 734 s, OK. Lượt này chạy trước bản sửa MEDIUM. Sau bản sửa: test không cần browser 26/26 OK; test browser chạy lại trong full suite.
- **Script của Codex trên code cuối:** 2/2 PASS; sha256 giống trước và sau.
- **Full suite trên code cuối, 23:54–00:30.** Chạy bằng `unittest discover` với `BILIFLOW_REQUIRE_E2E=1` và clip 1 s trong `input\` của worktree: 2.499 test, 2.122 s; 2.471 OK, 26 skip, 1 FAIL, 1 ERROR. **Chưa sạch trong một lượt.**
  - FAIL: `test_download_account_e2e.DashboardFlowTest.test_pc_sign_in_wakes_the_page_and_picked_episodes_land_in_input`. Dashboard thứ hai (giao diện tối) không hiện `#dl-accounts` trong 60 s khi máy đang tải nặng. Chạy lại riêng: 1/1 OK, 50 s.
  - ERROR: `test_adult_verification.VerifyAdultReportTests.test_model_failure_raises_without_writing`. Lỗi lúc test dọn thư mục tạm: WinError 32, file `movie.mp4` giả bị một process khác giữ. Test này không liên quan tới M7. Chạy lại riêng: 1/1 OK. Thư mục tạm nó để lại đã được xóa.
  - Skip giống các lượt trước: FFmpeg của module khác, Golden, studio-logo và các test opt-in. Không có skip nào trong test tải video hay test tài khoản.
  - page_script và provenance trong full suite: 25/25 OK.

**Còn chặn nghiệm thu thật (M7 chưa hoàn tất):** (a) host file trần; (b) bằng chứng logic trang vé ở trên; (c) instance thử với root và cổng riêng, bảo đảm probe không thể thành tải toàn bộ; (d) người dùng tự đăng nhập trong cửa sổ BiliFlow; (e) một vé bình thường và probe ≤ 1 MiB có deadline, đo Range, status, validator, yêu cầu cookie/Referer. Vì (b), `reads_tickets` còn False nên instance thử hiện không lấy được vé (TICKET_UNSUPPORTED). Hạn vé 1 giờ và tải tiếp 12 giờ cùng IP chỉ là lời trang, chưa đo; cùng máy không luôn là cùng IP; TTL phiên 3.600 s là ngưỡng dự phòng của app, chuyện riêng. Phiên: giữ `session_check=ttl`; `/ajax/notifications` là ứng viên kiểm phiên trực tiếp nhưng chưa có đối chứng hết hạn thật.

**Final-verify (2026-10-10; review FIX-REVIEW và prompt FINAL-VERIFY của Codex, ngoài repo):**
- **Method của yêu cầu đầu (P2 của review).** `_answers_this_click` tìm yêu cầu theo URL nhưng không kiểm phương thức, nên một GET tới đúng action, cùng opener/frame và chuỗi redirect, được nhận như POST của form. Sửa: mỗi mục có `request_method` (riêng tư như `request`, `repr=False`, không vào API hay log; `GET` hoặc `POST`; có `request` thì phải có method và ngược lại, sai thì mục không đọc được). Release-forms đặt `POST` cho form đã kiểm là POST. `navigation` thành `(page_url, trigger, reveal, request, method)`. `_answers_this_click` đòi yêu cầu đầu tiên khớp method của mục; các bước redirect sau nó là GET của trình duyệt, không áp quy tắc POST. Method được đọc lại cùng phim, tập, file, trigger, reveal và action ngay trước cú bấm (`_still_listed`, và `_ticket` khi quay lại trang): khác thì TICKET_TARGET_CHANGED/SourceChanged, không POST. Reader cũ không đổi (mục không có `request` vẫn không bao giờ nhận vé không mã). Regression trong repo: GET cùng action bị từ chối, cả popup và cùng tab; POST được nhận; method của mục quyết định (GET-mục nhận GET, POST-mục từ chối GET, mục thiếu method không nhận gì); mục đổi method hoặc action sau khi mở mùa không bị bấm (browser); config đọc mục: thiếu method, thiếu request, `PUT`, `post` viết thường đều không đọc được. Script của Codex chạy nguyên trạng: `download_account_m7_provenance_review_tests.py` (sha256 `fb95f6d3…364b9eed0`) trước sửa 1 PASS + 1 FAIL, sau sửa 2/2 PASS; `download_account_m7_review_tests.py` (sha256 `f2d9fb1a…a0cdcfb`) 2/2 PASS; sha256 cả hai giống trước/sau. Đột biến tối thiểu (bỏ hai dòng chốt method, file trả lại nguyên byte, sha256 khớp): hai regression mới trong repo FAIL, script Codex FAIL đúng test GET.
- **Công cụ probe nghiệm thu** `biliflow.download_account_probe` (`python -m biliflow.download_account_probe --root <root thử> --url <trang phim> [--file PHIM/TẬP/BẢN]`): chạy đúng `resolve` của provider một lần, không bao giờ gọi transfer (`FileTransfer`/worker), chỉ mô tả `ResolvedSource`. Chốt: root phải nằm hẳn trong `temp\` của bản cài (không phải root bản cài); giữ khóa `state\control-center.lock` của root suốt lượt (không chạy cạnh Control Center của root đó, vì khóa lượt của nguồn chỉ là khóa trong một process); `ticket_attempts=1`; marker `state\account-probe\ticket-asked.json` được ghi đồng bộ ngay trước cú bấm đầu tiên của lượt (hook mới `SessionBrowser(before_click=…)`, qua `browser_options`; ghi không được thì không bấm), nên probe bị giết sau cú bấm vẫn để lại marker, còn lượt chưa bấm (kể cả lỗi đăng nhập, mạng, hết giờ khi đọc danh sách) không để; root có marker bị từ chối (TICKET_ALREADY_ASKED) nếu không truyền `--another-ticket`; `BudgetHttp` bọc SafeHttp không cookie với một ngân sách ≤ 1 MiB cho mọi response: mỗi lần đọc chỉ xin tối đa phần còn lại, đóng response ngay khi người đọc đủ hoặc hết ngân sách (kể cả server bỏ Range), request sau khi hết ngân sách bị từ chối (PROBE_BUDGET); ngân sách chỉ tính byte thân của các response probe file qua `ctx.http` (header và trang portal/vé của trình duyệt ẩn không phải media, không tính); một deadline cho cả lượt (mặc định 330 s, hữu hạn, tối đa 900 s) và hủy (Ctrl+C thành hủy của lượt); reader production nên `reads_tickets=False` dừng trước mọi cú bấm. Báo cáo chỉ có mã, số đếm và dữ kiện header (status, Content-Range dạng số, Accept-Ranges, ETag mạnh/yếu, có Last-Modified, có Set-Cookie, byte đã đọc, đóng sớm), không link, vé, cookie, token, tiêu đề, tên file hay host. "Byte đã nhận" là byte BiliFlow lấy khỏi response; hệ điều hành có thể đã đệm thêm một ít trước khi đóng. Test `tests/test_download_account_probe.py` (15 lúc đầu, 19 sau review bên dưới): BudgetHttp với server bỏ Range (đọc đúng 1 MiB, đóng sớm, server không gửi hết file, request sau bị từ chối), không bao giờ xin quá phần còn lại, body ngắn tự kết thúc, câu trả lời bị từ chối ghi không kèm link, ngân sách tối đa 1 MiB; từ chối root bản cài/`temp`/ngoài temp/không tồn tại, Control Center của root đang chạy, marker, link không thuộc nguồn (không tạo thư mục tài khoản), dòng lệnh; trên Edge thật với fixture: phim lẻ một file tự resolve → đúng một POST, một GET Range `bytes=0-1048575` không cookie/Referer, không `media.part`, marker chặn lượt hai; server file bỏ Range → đọc đúng 1 MiB, đóng sớm, server dừng trước nửa file; reader production → TICKET_UNSUPPORTED, không POST, không marker; phim bộ → NEEDS_EPISODES không vé, file đã chọn → đúng vé của nó; deadline → DEADLINE, không lấy link. Fixture release-forms thêm `file_ranges` và `file_delay`.
- **Review chỉ đọc của lượt này (một agent, sau khi code ổn định; không chạy test):** 0 CRITICAL. HIGH đã sửa: marker trước đây chỉ ghi sau `resolve`, nên probe bị giết giữa chừng có thể cho lượt sau lấy vé thứ hai → nay ghi trong `before_click`. MEDIUM đã sửa: marker quá thô (lỗi trước cú bấm cũng để marker) → nay đúng theo cú bấm; `--seconds` không kiểm (nan/inf/quá lớn làm mất deadline) → hữu hạn, 0 < s ≤ 900; stdout UTF-8 cho thông báo tiếng Việt qua pipe; thiếu test `ticket_attempts=1` → test máy chủ file trả 403 cho link mới: TICKET_REFUSED với đúng một POST; thiếu test nhánh đọc lại khi quay về trang có file (`_ticket`) với method → test mới (đổi sang GET ở lần đọc 3 thì `_ticket` dừng bằng SourceChanged, trước `get_ticket`); thiếu test chốt form POST → test thẻ form GET. LOW đã sửa: `--file` kiểm từng id; nút submit có `formaction`/`formmethod` riêng làm thẻ không đọc được (cú bấm sẽ gửi khác action/method đã khai); manager do probe tạo được đóng, manager truyền vào phải là của root thử; Ctrl+C thành hủy của lượt; nhãn validator theo cùng quy tắc với `other_version`; method không phải chuỗi làm mục không đọc được; test deadline 30 s. LOW ghi lại, chưa sửa: route của mục vẫn là tuple 5 phần và `_still_listed` 10 tham số vị trí (đổi chỗ `request`/`request_method` không bị kiểu bắt; test vẫn bắt). Test probe nay 19; marker có trên đĩa trước khi trang vé được đọc (test), lượt lỗi trước cú bấm không để marker (test).
- **Host vé, quan sát ẩn danh** (trình duyệt phiên của BiliFlow, headless, không phiên, không vé, không bấm; chi tiết ở biên bản riêng): trang công khai của host vé không nêu host file nào (không preconnect/dns-prefetch, không CSP; tài nguyên chỉ là host vé, CDN, font và script quảng cáo, đều bị chặn); đường đăng nhập của host vé chuyển về trang đăng nhập của portal, nên host vé không có form đăng nhập riêng. Phiên của host vé được lập thế nào sau POST (callback, token trong chuỗi chuyển hướng hay cookie riêng) vẫn chỉ thấy được bằng một vé. **Host file trần vẫn chưa quan sát được**; `files` vẫn rỗng, không đoán, không wildcard.
- **Instance thử:** root `temp\m7-accept-20261010` của worktree (config tracked + config riêng; validator 0 lỗi), Control Center thử cổng 8797 `--no-import-existing`. Người dùng tự đăng nhập ở đó (Đăng nhập trên trang Tải video, cửa sổ riêng của BiliFlow); agent không bấm Đăng nhập, không POST route tài khoản, không mở hay in vault.
- **Nghiệm thu với phiên BiliFlow (2026-10-10 sáng, nguồn thật, không vé):**
  - Đăng nhập: NOT_CONNECTED → LOGGING_IN → **CONNECTED** do `NotificationsVerifier` nhận bằng chứng trong cửa sổ BiliFlow (người dùng tự gõ); `last_login` CONNECTED, không sót profile, không cảnh báo; TTL 3.600 s. Agent chỉ đọc trạng thái bằng GET. Control Center thử được dừng sau đó để nhả khóa root.
  - Cổng quảng cáo với phiên và ngoại lệ A (script ngoài repo; một lượt ẩn, chỉ host portal): trang phim lẻ 200, đã đăng nhập, 1 form tải; script cổng được trả lời 1 lần; dialog cổng có nhưng đóng, trang không bị khóa; reader đọc 1 mục có action, method POST; Playwright `click(trial=True)` trên nút lấy vé: actionable (kiểm khả năng bấm, không bấm); 0 yêu cầu tới action của form. Mọi host quảng cáo và bên thứ ba khác bị chặn (HOST_NOT_ALLOWED). Phiên xoay vòng được lưu.
  - Probe nghiệm thu: phim bộ → NEEDS_EPISODES, danh sách đầy đủ (2 mùa × 6 tập, 12 file), 0 byte, không marker; phim lẻ → tự chọn file duy nhất rồi **TICKET_UNSUPPORTED trước cú bấm**, 0 byte, không marker. **Không lấy vé nào, không nhận byte media nào.**
  - Dừng trước cú bấm theo review: chưa đủ chứng cứ để bật reader (bên dưới).
- **Ngoại lệ A giữ đúng phạm vi thử** (không thêm host, tài nguyên, không áp cho cửa sổ đăng nhập). Việc người dùng duyệt các thao tác sửa code không phải là chấp nhận rủi ro đưa A vào bản chính.
- **Đề xuất cập nhật invariant Network của `AGENTS.md` (để Codex/người dùng review; chưa áp dụng, invariant hiện tại giữ nguyên):**

  > - Network. Every request of the authenticated browser keeps the downloader's network checks: public addresses only, DNS pinned at the connection, TLS checked, checked redirects, bounded reads and a deadline. Cookies go only to their own source's hosts. One exception (M7 exception A, a trial that is off unless a source's Git-ignored local config sets `page_script`): a hidden run that may ask for a ticket (never the sign-in window, never a list-only run) may load exactly one configured HTTPS script URL outside the source's hosts. Only a GET of resource type `script` to that exact host and path is answered; the page's own query (`_=<digits>`) is checked and never forwarded, the configured URL is fetched as written; it carries only Accept and User-Agent (no cookie, Authorization, Referer or Origin); a redirect is refused and no Set-Cookie reaches the page (only the content type and `no-store`); at most 4 such requests per run, 2 MiB and 20 seconds each, through the same checked client. Every request the script makes in turn still goes through the route handler and is refused unless the rules above allow it. The script runs with the page's rights (it can read the page and its non-HttpOnly cookies); this is not a sandbox. Real hosts and URLs live only in the local config. Turning A on in the main copy is a separate decision, recorded with its date when the user makes it; it is never inferred from approved code changes or from a trial.

- **Còn chặn sau final-verify (M7 chưa hoàn tất):**
  1. Host file trần: chưa quan sát được (trang công khai của host vé không nêu; extension che ở lần khảo sát Chrome). `files` rỗng nên dù có vé, link tải sẽ bị từ chối (TICKET_LINK_REFUSED) và không probe được.
  2. Hợp đồng trang vé trong trình duyệt của BiliFlow: các bước từ POST tới trang vé, cách host vé lập phiên của nó (đường đăng nhập của host vé dẫn về portal; phần còn lại chỉ thấy bằng một vé), nghĩa của countdown và `ready`/`success`/`blocked`, cổng quảng cáo riêng của trang vé. Vì vậy `reads_tickets` vẫn False.
  3. Một vé và probe ≤ 1 MiB (status, Range, validator, cần cookie/Referer hay không): chưa làm. Audio/video của file thật chưa kiểm.
  4. Ngoại lệ A vào bản chính: chờ quyết định riêng của người dùng (đoạn đề xuất ở trên).
- **Cần người dùng/Codex quyết (agent không tự làm):** (a) cách lấy host file trần: người dùng tự đọc host (chỉ phần host) từ lịch sử tải của chính họ nếu đã từng tải từ nguồn này, hoặc chấp nhận một lượt quan sát tốn vé; (b) có cho một lượt nghiệm thu bật đọc vé chỉ trong lượt đó (công cụ probe, root thử, một vé, không bấm nút tải, ghi chuỗi điều hướng và trạng thái trang vé dạng che) trước khi hợp đồng trang vé được chứng minh hay không. Không có (a) thì một vé chỉ cho bằng chứng hợp đồng, không cho probe; probe sau đó cần vé thứ hai, vượt ngân sách một vé hiện tại.
- **Kết quả test final-verify (code cuối):** focused/E2E (provenance, listing, release_forms, sources, page_script, probe, browser, config, login, E2E, E2E flows, crossing; `BILIFLOW_REQUIRE_E2E=1`) trước bản sửa review: 281 test, 1.306,5 s, OK, 0 skip; sau bản sửa review, các module đã đổi (probe, provenance, listing, release_forms, sources): 145 test, 536,6 s, OK; hai script Codex trên code cuối: 2/2 mỗi script (sha256 không đổi); đột biến nhắm vào bản sửa review: 3/3 bị bắt. full suite trên code cuối, một lượt (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree), 2026-10-10 09:44–10:18: 2.523 test, 2.068,4 s, OK: 2.497 đạt + 26 skip, 0 FAIL, 0 ERROR (skip cùng lý do như trước; không có skip nào trong test tải video hay tài khoản; log `m7\logs\full-2.log` trong scratchpad của phiên). Lượt này thay cho lượt trước có 1 FAIL + 1 ERROR; hai lỗi đó không lặp lại.

**Quan sát một vé (2026-10-10 chiều; prompt `BILIFLOW-SOURCE-ACCOUNTS-M7-OBSERVE-TICKET-PROMPT.md`, ngoài repo):**
- **Chế độ quan sát riêng** `biliflow.download_account_observe` (`python -m biliflow.download_account_observe --root <root thử> --url-file <file> [--file PHIM/TẬP/BẢN] [--save-host [--probe-same-ticket]]`). Trong lượt đó reader production giữ `reads_tickets = False`; chế độ này không đăng ký reader nào và không đổi `RELEASE_FORMS_READER`.
  - Dùng lại đúng đường của bộ tải tới cú bấm: đọc danh sách bằng phiên, mở mùa nếu gập, đọc lại trang ngay trước cú bấm, đúng một nút khớp, rồi kiểm nguồn gốc vé (`_TicketFlow.find`/`_answers_this_click`). Chỉ khác: `get_ticket(flow_type=…)` nhận `ObserveFlow` thay cho `_TicketFlow`. `ObserveFlow` theo dõi trang vé thay vì lấy link. `ObservingReader` báo trang vé "sẵn sàng" thành "đang chờ", nên không luồng nào lấy được link qua nó. `ObservingProvider.resolve` từ chối (OBSERVE_ONLY): không worker, không transfer, không probe của provider.
  - Chốt của công cụ probe giữ nguyên: chỉ root trong `temp\`; giữ khóa Control Center của root; marker trước cú bấm đầu tiên; root có marker bị từ chối nếu không có `--another-ticket`; một lượt ẩn, một deadline (mặc định 480 s, lượt ẩn 300 s, trang vé 210 s); hủy bằng Ctrl+C.
  - Thêm một chốt marker: yêu cầu đầu tiên không phải GET (một trang tự submit) ghi marker trước khi được trả lời; ghi không được thì yêu cầu đó bị từ chối (OBSERVE_MARK_FAILED).
  - Trước cú bấm, có điều hướng không phải GET hoặc có yêu cầu tới action của mục thì dừng, không bấm (UNEXPECTED_SUBMIT). Sau cú bấm, yêu cầu thứ hai tới action đó kết thúc lượt (EXTRA_SUBMIT). Cú bấm mở mùa không tính là cú bấm vé (`reveal_clicks` riêng).
  - Trong trình duyệt (hook mới `SessionBrowser(request_hook=…)`, chỉ được từ chối thêm, không cho phép thêm gì): mọi yêu cầu được đếm theo method, vai trò host và loại tài nguyên; đường dẫn có đoạn `download` hay `play` bị từ chối (OBSERVE_REFUSED). Danh sách tên này chỉ là chốt báo động. Chốt thật: host file không thuộc host của trình duyệt (portal và vé), và trình duyệt phiên vốn hủy yêu cầu media. Quá 20.000 URL khác nhau thì dừng (REQUEST_LOG_FULL), không đếm thiếu.
  - Ghi theo thời gian: trạng thái reader của trang vé, thuộc tính `a#downloadBtn` (`data-state` và `aria-disabled` chỉ khi là từ trạng thái, `href` dạng che) vào `events` (trạng thái cuối luôn được ghi); số đếm ngược vào `ticks`.
  - Link của trang sẵn sàng chỉ ở trong bộ nhớ. Host trần chỉ được dùng khi link là https, cổng mặc định, không user, và host không phải portal, vé hay host script. Với `--save-host`, host được ghi một mình vào vai trò `files` của config riêng của root thử, chỉ khi vai trò đó rỗng hoặc đã đúng host đó. File config được thay một lần (file tạm rồi `os.replace`), rồi đọc lại; còn lỗi thì trả lại nguyên byte cũ. Không bao giờ thay một host khác.
  - Với `--probe-same-ticket` (cần `--save-host`): probe đúng link đó, không lấy vé mới. Dùng `resolve_file` qua SafeHttp không cookie, không Referer, sau `BudgetHttp` ≤ 1 MiB cho mọi response, đóng sớm kể cả khi server bỏ Range. Lỗi sau trạng thái sẵn sàng vẫn ghi báo cáo (`after_ready_error`).
  - Báo cáo `state\account-probe\observation-<thời điểm>.json` chỉ có:
    - mã, số đếm, thời gian;
    - dạng đường dẫn: vai trò host, các từ cấu trúc của `PATH_WORDS`, `{n}`, `{tok}`;
    - dữ kiện chữ của trang (`text_facts`: độ dài, số nhỏ, từ trong một bộ từ cố định, có hiện địa chỉ IP hay không).

    Không có URL, host, token, cookie, giá trị form, địa chỉ IP, e-mail, tên phim hay tên file.
- **Chốt mới trong production** (`download_account_pages._check_submission`, gọi trong `get_ticket` ngay trước cú bấm khi mục có `request`):
  - Nút khớp phải gửi đúng `request` và method của mục theo thuật toán submit của HTML (`download_account_browser.effective_submission`):
    - form chủ (kể cả thuộc tính `form=` trỏ nơi khác);
    - `formaction`/`formmethod` đè lên form;
    - action rỗng là URL của tài liệu; method sai là GET; `dialog` không gửi gì;
    - nút bị tắt, kể cả trong `fieldset[disabled]`, bị từ chối.
  - Nút hay form có `onclick`/`onsubmit` cũng bị từ chối. Khác thì TICKET_TARGET_CHANGED, không bấm.
  - Thẻ release-forms có hơn 8 nút submit, hoặc nút nào có `formaction`/`formmethod` (kể cả rỗng), thì không đọc được.
  - Test cũ `test_the_page_read_again_on_the_way_back_must_keep_the_entrys_method` (sources) đổi kỳ vọng: fixture chung có trigger là link `<a>` không submit gì, nên mục được reader gán một POST giả không còn được bấm. Lượt không đổi giờ dừng ở chốt mới (TICKET_TARGET_CHANGED); lượt đổi method vẫn dừng ở `_ticket` (SourceChanged) như trước.
- **Test** (fixture `.example`, Edge headless thật):
  - `tests/test_download_account_observe.py`, kiểm:
    - vé sẵn sàng sau đúng một POST, không yêu cầu nào tới host file;
    - lưu host trần, rồi probe chính vé đó: một GET Range `bytes=0-1048575` không cookie/Referer, cùng token;
    - `blocked` → TICKET_CHALLENGE, không host, không probe;
    - trang tự submit → UNEXPECTED_SUBMIT, không cú bấm, có marker, lượt hai bị từ chối;
    - nút `form=` trỏ form khác → không bấm;
    - submit thứ hai bằng script → EXTRA_SUBMIT, không link;
    - đường dẫn `/play` và `/download` bị chặn trong trình duyệt;
    - trang không bao giờ sẵn sàng → TICKET_TIMEOUT;
    - root có marker → bị từ chối;
    - phim bộ: cú bấm mở mùa không phải cú bấm vé;
    - lỗi sau trạng thái sẵn sàng vẫn có báo cáo;
    - dữ kiện chữ không lộ IP, e-mail, host hai nhãn, tên file;
    - lưu host lỗi giữ file và không để file tạm;
    - trạng thái cuối được ghi khi vượt giới hạn;
    - bảng `effective_submission`;
    - dòng lệnh.
  - Fixture release-forms thêm `auto_submit`, `extra_html`, `ticket_extra` và popup `double-submit`.
  - Regression production `test_a_button_that_would_send_another_request_is_never_clicked`: nút `form=` trỏ nơi khác, có `onclick`, hoặc bị tắt đều không bị bấm và không POST.
- **Review chỉ đọc** (một agent, sau khi code ổn định): 0 CRITICAL, 1 HIGH, 5 MEDIUM, 2 LOW; đã sửa tất cả trước lượt thật.
  - HIGH: chữ trang vé được che bằng danh sách đen. Nó lọt host hai nhãn, IP, e-mail, tên file, và selector "count" khớp cả `account`. Đổi sang danh sách trắng `text_facts`, selector đếm ngược chặt hơn.
  - MEDIUM:
    - `shape` giữ mọi từ ≤ 24 chữ, nên tên phim có thể lọt; đổi sang danh sách từ `PATH_WORDS`.
    - Cú bấm mở mùa bị tính là cú bấm vé; đã tách.
    - Trang tự submit không để marker; đã thêm marker khi có yêu cầu không phải GET.
    - Lỗi sau trạng thái sẵn sàng làm mất báo cáo và có thể để file tạm; đã bọc và thay file một lần.
    - Giới hạn 200 sự kiện có thể làm mất trạng thái sẵn sàng; nay đếm ngược ở `ticks` và trạng thái cuối luôn được ghi.
  - LOW:
    - Bộ đếm URL giờ dừng lượt khi đầy, không đếm thiếu.
    - Docstring nói rõ danh sách `download`/`play` không phải chốt chống media.
- **Test trước lượt thật:**
  - Test fixture mới của chế độ quan sát lúc đầu đạt 19/19.
  - Lượt đầu các module liên quan (observe, release_forms, probe, provenance, listing, sources, page_script, browser): 241 test, 1 ERROR. Test sources ở trên còn kỳ vọng cũ; đã sửa test.
  - Sau bản sửa review, các module đã đổi (observe, release_forms, sources, provenance, probe; `BILIFLOW_REQUIRE_E2E=1`): 136 test, 614,5 s, OK.
  - Hai script Codex chạy nguyên trạng, 2/2 mỗi script. Sha256 trước và sau: `f2d9fb1a…a0cdcfb`, `fb95f6d3…364b9eed0`.
- **Lượt thật (2026-10-10, nguồn thật, root thử `temp\m7-accept-20261010`, phiên BiliFlow):**
  - Đăng nhập: Control Center thử cổng 8797 `--no-import-existing`. Người dùng tự bấm Đăng nhập lại và tự đăng nhập trong cửa sổ của BiliFlow: NEEDS_LOGIN → LOGGING_IN (11:40) → **CONNECTED** (11:42 +07). Không sót profile, không cảnh báo. Agent chỉ đọc trạng thái bằng GET. Control Center thử được dừng trước lượt quan sát (khóa root), không còn process nào của root.
  - Lượt quan sát: chạy **đúng một lần**, 11:42:35–11:44:16, 100,7 s, phim lẻ (link người dùng đã đưa, đọc từ file riêng trong root thử, không in). Kết quả **READY**.
    - Cú bấm vé: 1. Cú bấm mở mùa: 0.
    - **POST vé thực tế: 1.** Hook đếm: 0 yêu cầu không phải GET trước cú bấm, 1 sau. Yêu cầu tới action của mục: 0 trước, 1 sau. Không auto-submit, không challenge. Marker được ghi trước POST.
  - Chuỗi điều hướng (dạng che; bản ghi đủ, 7 bước):
    1. POST `portal:/download-links/{n}/access` (tab mới);
    2. `tickets:/x/{tok}`;
    3. `tickets:/login?{q}`;
    4. `tickets:/{tok}/{tok}?{q}`;
    5. `portal:/{tok}/{tok}/{tok}?{q}`;
    6. `tickets:/{tok}/{tok}/{tok}?{q}`;
    7. `tickets:/x/{tok}` 200.

    Như vậy host vé **tự lập phiên từ phiên portal bằng chuyển hướng trong chính chuỗi của cú bấm**: 6 chuyển hướng sau POST, dưới MAX_HOPS 10. Không cần đăng nhập riêng, không cần ngoại lệ mạng mới.
  - Cookie: trước cú bấm 2 của portal, 0 của host vé; sau đó 2 của portal, 2 của host vé.
  - Nguồn gốc vé (`_answers_this_click`) nhận trang vé 10,0 s sau cú bấm.
  - Trang vé: một `a#downloadBtn`. Lúc thấy: `data-state=loading`, `aria-disabled=true`, `pointer-events-none`, `href` dạng `other:/x/{tok}/download`; chữ nút 26 ký tự, có từ "quảng cáo" (trang đang kiểm quảng cáo).
    - Đếm ngược 8 → 1, mỗi bước khoảng 1,1 s.
    - Tại 18,0 s: `success`, mở khóa, chữ nút "tải xuống ngay". Reader production nói ready đúng lúc đó.
    - Cổng quảng cáo của trang vé không chặn trong trình duyệt BiliFlow (không có `blocked`).
    - Trang có 1 form POST, 0 ô mật khẩu, 0 link đăng nhập/đăng xuất.
  - Lời trang (chỉ dữ kiện chữ, **không phải kết quả đo**): link hết hạn sau 1 giờ; tải tiếp trong 12 giờ trên cùng IP/mạng; một câu về địa chỉ mạng; một câu bảo tải lại trang nếu chặn quảng cáo. Trang không hiện địa chỉ IP nào.
  - Link: https, cổng mặc định, không user, query hay fragment, 101 ký tự, host không thuộc vai trò nào của nguồn. **Host trần được ghi một mình vào vai trò `files` của config riêng của root thử** (đọc lại 0 lỗi). Không file tracked nào có host.
  - **Probe chính vé đó**, 2,9 s sau ready: một GET Range qua SafeHttp không cookie, không Referer.
    - 206, `video/x-matroska`, Content-Length 1.048.576, Content-Range `0-1048575/18661785164` (~17,4 GiB).
    - Accept-Ranges gửi hai lần (`bytes, bytes`), BiliFlow vẫn nhận là có Range.
    - ETag mạnh, có Last-Modified, không Set-Cookie.
    - Đọc đúng 1.048.576 byte.
    - ffprobe trên mẫu: Matroska, video HEVC 3840×1918, audio AAC, dài 5.900,3 s (từ header của mẫu).
    - Host file **không cần cookie hay Referer** cho yêu cầu này.
  - **Tổng byte media: 1.048.576 (1 MiB)**, đúng ngân sách. Trình duyệt không gửi yêu cầu nào tới host file và không mở link /download hay /play. Hook không phải từ chối yêu cầu nào; các host quảng cáo và bên thứ ba bị chặn ở bước kiểm host của trình duyệt như trước.
  - Script ngoại lệ A được trả lời 1 lần trong lượt.
  - Báo cáo: `state\account-probe\observation-20261010T044416679016Z.json` của root thử. Secret scan: 0 host, link, IP hay e-mail. Marker của root ghi `READY`: lượt sau bị từ chối nếu không có `--another-ticket`.
- **Hoàn thiện theo bằng chứng:**
  - Docstring của `download_account_release_forms` ghi hợp đồng đã đo (cấu trúc, không host).
  - Fixture release-forms thêm `ticket_sso`, đúng dạng chuỗi trên: `/login` của host vé, bước riêng, ủy quyền ở portal, callback đặt cookie `tks` trên 302. Chữ nút lúc đếm ngược nói đang kiểm quảng cáo.
  - Test mới:
    - production flow (`TicketReader`) đi qua chuỗi 6 chuyển hướng, lưu cookie host vé vào phiên, lượt sau vào thẳng trang vé (`sso_logins` = 1), host file không nhận cookie;
    - chế độ quan sát ghi đúng chuỗi 7 bước như lượt thật.
- **`reads_tickets` khi đó vẫn False** (đã bật cùng ngày: mục "Bật reader và đóng M7" bên dưới). Agent định bật nó theo bước 7 của prompt; bộ phân loại an toàn của auto mode từ chối thay đổi đó ("Security Weaken"), và agent không lách. Bật reader là quyết định của người dùng/Codex dựa trên bằng chứng trên. Chưa thấy: `blocked`, `error`, trang vé hết hạn hoặc mất phiên. Các trạng thái này kết thúc bằng từ chối hay hết giờ, không bao giờ cho link. Khi được duyệt, thay đổi gồm:
  - `FilmPageReader.reads_tickets = True`;
  - đổi các test đang khẳng định reader production không tốn vé (`RegistryTest`, `TicketTest.test_the_production_reader_spends_no_ticket`, `test_download_account_probe…production_reader_stops…`) sang một reader thử có `reads_tickets=False`;
  - chạy lại các module đó.
- **Kết quả test trên code cuối:**
  - Sau khi hoàn thiện theo bằng chứng, các module observe, release_forms và probe (`BILIFLOW_REQUIRE_E2E=1`): 88 test, 342,5 s, OK.
  - Full suite một lượt (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree), 2026-10-10 11:55–12:32: 2.551 test, 2.207,4 s, **OK: 2.525 đạt + 26 skip, 0 FAIL, 0 ERROR**. Skip cùng lý do như trước; không có skip nào trong test tải video hay tài khoản.
  - Log `m7obs\full.log` trong scratchpad của phiên.
- **Dọn dẹp:** Control Center thử đã dừng (TaskStop), không còn process nào của root thử; tab trình duyệt của instance thử đã đóng; file link riêng trong root thử đã xóa; thư mục tạm và profile trình duyệt của lượt trống. Còn giữ, có chủ ý:
  - root thử, với phiên mã hóa của người dùng (hết hạn theo TTL lúc 12:42 +07), marker, báo cáo và host file trong config riêng;
  - bản sao báo cáo đã che trong scratchpad.

  Xóa root thử khi M7 kết thúc hoặc bị bỏ.

**Bật reader và đóng M7 (2026-10-10; review `BILIFLOW-SOURCE-ACCOUNTS-M7-READY-REVIEW.md` và prompt `…-M7-ENABLE-READER-PROMPT.md`, ngoài repo):**
- Người dùng đồng ý bật reader theo review của Codex. `FilmPageReader.reads_tickets = True` được sửa thành một thay đổi riêng trong phiên này để người dùng duyệt thủ công như họ yêu cầu; lần này thay đổi không bị từ chối, và không công cụ nào khác được dùng để đi vòng. Ngoài dòng đó, production chỉ đổi docstring (release_forms, observe).
- Docstring của `download_account_release_forms` ghi hợp đồng đã quan sát và giới hạn:
  - chưa thấy `blocked`, `error`, trang vé hết hạn hoặc mất phiên;
  - lời trang về hạn link 1 giờ và tải tiếp 12 giờ chưa được đo;
  - một Range từ byte 0 với ETag mạnh không chứng minh resume giữa nhiều vé hay mạng.

  Các trạng thái đó không bao giờ cho link: challenge → SOURCE_CHALLENGE, error → TICKET_FAILED; trang rời đi, đóng hoặc không sẵn sàng kết thúc khi hết lượt chờ vé (TICKET_FAILED, TICKET_TAB_CLOSED, TICKET_TIMEOUT).
- Giữ nguyên mọi chốt:
  - request và method của mục;
  - nút được bấm phải gửi đúng request/method (`_check_submission`);
  - nguồn gốc vé chỉ qua chuyển hướng của chính cú bấm (`_answers_this_click`);
  - đếm ngược của trang;
  - link chỉ trên host file đã cấu hình (TICKET_LINK_REFUSED);
  - chốt mạng và phiên;
  - ngoại lệ A ở phạm vi thử.
- Test:
  - `RegistryTest`: reader đã đăng ký là `FilmPageReader` và đã bật; registry chỉ đọc; adapter chưa có reader (`ticket-files`) vẫn READER_UNSUPPORTED.
  - Hai test chứng minh reader tắt không tốn vé dùng `ListOnlyReader` (`reads_tickets = False` riêng), không monkeypatch đối tượng production: `TicketTest.test_a_reader_that_reads_no_tickets_spends_no_ticket` và `ProbeTest.test_a_reader_that_reads_no_tickets_stops_before_the_click_and_leaves_no_marker`. Giữ đủ assertion cũ, thêm: 0 cú bấm, không marker, không yêu cầu tới host file, không yêu cầu nào không phải GET.
  - Mới `ProductionReaderTest`: không truyền reader; provider tự lấy `RELEASE_FORMS_READER` từ `PAGE_READERS` cho nguồn cấu hình đúng dạng sản phẩm (adapter `release-forms`, host file, `page_script` của ngoại lệ A). Một vé đi qua chuỗi đăng nhập 6 chuyển hướng của host vé và đếm ngược 2 s, với các kiểm:
    - đúng một yêu cầu không phải GET (POST của form);
    - link chỉ được dùng sau đếm ngược;
    - host file không nhận cookie hay Referer;
    - host vé không nhận cookie của portal;
    - cookie của host vé được lưu vào phiên.
  - Test observe: tạo `ObservingReader(RELEASE_FORMS_READER)` không đặt gì lên reader production (`vars` rỗng).
  - Đột biến trên bản sao `src` trong `temp\m7-mutants`; file của worktree không bị ghi, thư mục đã xóa:
    - tắt flag → `RegistryTest` và `ProductionReaderTest` hỏng;
    - ready bỏ qua khóa → `ProductionReaderTest` hỏng (đếm ngược);
    - bản sao không đổi thì đạt.
- Không lấy vé mới, không chạm nguồn thật, không probe lại; không dùng `--another-ticket`; marker READY và root thử được giữ.
- **Kết quả test trên code cuối:**
  - 11 module bị tác động (release_forms, probe, observe, sources, page_script, provenance, listing, login, E2E, E2E flows, crossing; `BILIFLOW_REQUIRE_E2E=1`): 245 test, 1.310,5 s, OK, 0 skip. Sau lượt này chỉ sửa câu chữ docstring của reader, không đổi code.
  - Hai script Codex chạy nguyên trạng: 2/2 mỗi script, sha256 không đổi trước và sau.
  - Full suite trên code cuối, một lượt (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree), 2026-10-10 13:30–14:07: 2.553 test, 2.216,8 s, **OK: 2.527 đạt + 26 skip, 0 FAIL, 0 ERROR**. Skip cùng lý do như trước; không có skip nào trong test tải video hay tài khoản.
  - Log trong scratchpad của phiên: `m7\logs\focused-enable.log`, `m7\logs\full-enable.log`, `m7\logs\codex-enable-*.log`, `m7\logs\mutate-enable.log`.

### 9.22 Dùng lại vé, giữ link trong bộ nhớ, NO_INPUT_DIR giữ file, log loại dấu phiên bản (2026-10-10 tối; Codex review đạt; đã chép vào bản tích hợp)

Bản tích hợp không có mục 9.21 của worktree tính năng; các sửa đó được ghi ở §10 và trong `SESSION_HANDOFF.md` của bản này. Nội dung mục này chép từ §9.22 của worktree tính năng; phần "Chép vào bản tích hợp" ở cuối là của bản này.

Codex đóng đợt thử P1/P3 và giao bốn mục bằng prompt TICKET-REUSE (nằm ngoài repo). Làm và kiểm ở worktree tính năng. Codex review đạt (review ngoài repo); sau đó delta được chép vào bản tích hợp này đêm 2026-10-10 để người dùng tự thử (chi tiết trong `SESSION_HANDOFF.md` của bản này). Chưa commit/merge/push; bản chính không đổi. Không lấy vé, probe hay media thật nào; mọi số dưới đây là của fixture.

1. **Vé của lần thăm dò phục vụ lượt tải đầu.**
   - `download_account_tickets.TicketCache` (mới): chỉ trong RAM của process, một entry mỗi tác vụ, chỉ cho file HTTP của tài khoản nguồn có `ResolvedSource.issuer`. yt-dlp, provider ẩn danh và HLS không đổi.
   - `issuer` = `TicketIssuer(owner (root, SID), source_id, generation)`. Generation là của lease mà lượt ẩn dùng để lấy vé, gắn trong `_session_run` ngay sau kiểm `_current(lease)`, không bao giờ là generation đọc về sau. `issuer` không vào repr, `public()` hay phép so sánh.
   - `_probe_source` lấy token (`begin`) trước resolve và chỉ ghi entry (`fresh`) sau khi PROBING → WAITING_SPACE thành công. Trang đang chờ chọn tập/bản không có vé nên không có entry.
   - `_download_source`: `take` phải khớp attempt, identity của probe, owner, nguồn và generation của phiên dùng được lúc này (đọc từ hàng tài khoản, không đọc kho phiên). Khớp và còn `fresh` thì dùng ngay link của probe: không kiểm lại, không lượt ẩn thứ hai. "Còn fresh" chỉ trong 120 giây sau probe (`FRESH_SECONDS`); tác vụ đã chờ lâu hơn (chờ chỗ trống) thì kiểm link trước như một lần Tiếp tục, vẫn không lấy vé. Sự kiện: "Dùng link tải vừa lấy lúc thăm dò; bắt đầu tải (không lấy vé lần hai)."
2. **Giữ link qua Dừng/Tiếp tục ngắn.**
   - Giới hạn của cache (không phải tuổi vé): 100 entry, đầy thì bỏ entry nhàn rỗi cũ nhất; 10 phút nhàn rỗi theo đồng hồ monotonic, mốc đặt khi probe xong và khi lượt kết thúc STOPPED, INTERRUPTED, QUEUED hay WAITING_SPACE. Entry mà transfer đang dùng không hết hạn, nên cache không bao giờ ngắt transfer.
   - Lượt sau kiểm link bằng probe HTTP giới hạn sẵn có (`AccountSourceProvider.recheck`: một GET Range 1 MiB qua SafeHttp không cookie; không trình duyệt, không vé). Plan của nguồn lấy theo chính câu trả lời đó (validator, dung lượng; cùng URL không có nghĩa cùng dấu), rồi transfer quyết định như với mọi link: cùng dấu thì Range/If-Range nối tiếp; dấu khác thì so toàn bộ phần (P1); không có dấu thì tải lại từ đầu theo strict.
   - Nhánh dự phòng:
     - 401/403/404, không phải video, khác dung lượng lúc thăm dò: bỏ entry, đi đúng một lượt resolve mới có giới hạn như cũ (một vé). Không WAITING_LOGIN.
     - Không có câu trả lời (NETWORK, DNS, thân bị cắt): INTERRUPTED, giữ entry, không vé, không đăng nhập.
     - 429/5xx (máy chủ có trả lời): INTERRUPTED và bỏ entry, nên lần Tiếp tục sau lấy một vé mới thay vì ghim link máy chủ không phục vụ. Transfer kết thúc SERVER_BUSY cũng bỏ entry.
   - Mỗi refresh thành công trong lượt thay entry bằng link mới nhất. Kết quả muộn hay lỗi không đè entry mới hơn (token).
   - Bỏ entry khi: Hủy, Xóa, dọn file tạm và hết 7 ngày, dọn hàng sau 30 ngày, Thử lại (attempt mới), chọn lại tập/bản, chờ đăng nhập, tác vụ kết thúc, file đã tải xong (lượt sau chỉ kiểm và chuyển, không cần link), Ngắt kết nối và đăng nhập mới (hook `AccountRuntime._session_changed` → `forget_source_tickets`), shutdown (`clear`). Restart thì cache mất; không khôi phục link từ file hay log.
   - Race: `revoke` của tác vụ, và `revoke_source` của nguồn (mốc theo owner và nguồn, áp cho mọi tác vụ, kể cả chưa có entry), từ chối mọi kết quả resolve bắt đầu trước đó. Một refresh bắt đầu sau lần đăng nhập mới, trong cùng lượt, vẫn được ghi (`began`). Sau lần kiểm link (một request), generation được đọc lại và kết quả `put` được xét. Nếu phiên đổi giữa chừng (Ngắt kết nối, đăng nhập mới, quá tuổi) thì link đó không được dùng; lượt đi đường resolve, tới WAITING_LOGIN hay vé của phiên mới.
   - Link mới lấy (lấy lại nguồn đầu lượt, hay refresh giữa lượt) mà cache từ chối vì phiên đổi trong lúc lấy thì không được dùng. Lượt lấy lại đúng một lần (`_fresh_kept`); lần đó gặp cổng đăng nhập (WAITING_LOGIN, phần giữ nguyên) hoặc lấy vé của phiên mới. Phiên đổi tiếp thì INTERRUPTED ACCOUNT_STATE_ERROR.
   - Mỗi lượt điều phối bỏ khỏi bộ nhớ link nhàn rỗi quá hạn và link của phiên không còn dùng được (hết tuổi, bị từ chối, bị thay; `_drop_stale_tickets`), không chờ tới lần tác vụ chạy lại. Hook bỏ link chạy cả khi Ngắt kết nối lỗi giữa chừng (`finally`).
   - Cache không vượt login_gate: tác vụ chờ đăng nhập vẫn chờ; generation không khớp là miss.
   - Bí mật: link, vé và header không vào DB, JSON, log, sự kiện, API, repr hay Git. `TicketCache` chỉ hiện số entry; `Reuse`, `_Entry` và `TicketIssuer` ẩn link và owner khỏi repr.
3. **NO_INPUT_DIR giữ file.**
   - Thiếu `input` lúc chuyển (kể cả `input` bị xóa giữa lúc đổi tên) thì PUBLISHING → INTERRUPTED NO_INPUT_DIR. File đã kiểm tra giữ trong thư mục tạm; BiliFlow không tự tạo `input`. Thông báo: tạo lại thư mục input trong thư mục BiliFlow rồi bấm Tiếp tục.
   - Tiếp tục: `finished_media` chạy trước cả việc tìm provider, nên nguồn đã bị bỏ khỏi config vẫn chuyển được. Lượt bỏ qua chờ đăng nhập và chờ chỗ trống; không vé, không probe, không request media. File được kiểm tra lại đầy đủ, và SHA-256 phải bằng của lần kiểm trước (`reused_finished`); khác thì FAILED FILE_CHANGED, không chuyển. Đường yt-dlp không đổi (cờ chỉ đặt ở đường file của provider).
   - Tiếp tục khi `input` vẫn chưa có: báo lại NO_INPUT_DIR ngay, không giải mã và băm lại file (có thể nhiều GB).
   - Thông báo NO_INPUT_DIR dùng chung với yt-dlp nên không hứa "không tải lại": tác vụ yt-dlp khi Tiếp tục chạy lại yt-dlp và chờ chỗ trống như thường (trước đây là FAILED).
   - **Ghi chú dựng root thử (sửa):** root thử phải có `input\` đúng root trước lần nghiệm thu chuyển file. Đợt thử tác vụ 5 thiếu nó và kết thúc FAILED NO_INPUT_DIR với code cũ. Root đang dùng và tác vụ thật không bị sửa trong đợt này.
4. **Log loại dấu phiên bản** (đường strict, tức file của tài khoản nguồn).
   - Mẫu dòng:
     - đầu lượt: "Link của lượt này: <dấu>; phần đã tải lưu dấu: <loại>." hoặc "…; chưa có phần đã tải.";
     - mỗi câu trả lời: "Trả lời HTTP <status> (<đã gửi>): <dấu>.";
     - dấu lưu cho phần: "Dấu lưu cho phần đã tải: <loại> (…)";
     - sau refresh: "Link mới: <dấu>.";
     - lúc thăm dò: "Thăm dò link tải: <dấu>."; lúc kiểm link còn giữ: "Kiểm lại link đã giữ: <dấu>." (ghi ngay cả khi tác vụ dừng trước lượt tải).
   - <dấu> gồm ETag mạnh/yếu/không có, có/không có Last-Modified, và dấu dùng được: ETag, Last-Modified hay không có. Không ghi giá trị, hash, URL hay host. Một câu trả lời thiếu dấu chỉ được ghi cho chính câu trả lời đó.
   - Câu cũ "nguồn thật có ETag" (§9.21 của worktree tính năng; ở bản này là ghi chú review sửa 3–4 trong `SESSION_HANDOFF.md`) đã sửa theo bằng chứng.
   - Thông báo Dừng/INTERRUPTED không hứa luôn tải nối: "…bấm Tiếp tục để tải nối nếu nguồn còn đúng phiên bản file, nếu không thì tải lại từ đầu."

- **Phản biện thiết kế trước khi code (workflow 3 góc: race, bảo mật, test; không chạy test).** Đã sửa:
  - một lần Ngắt kết nối hay đăng nhập mới trong lúc kiểm link vẫn cho transfer chạy bằng link cũ (giờ kiểm `put` và generation sau lần kiểm);
  - 429/5xx ghim link qua mọi lần Tiếp tục;
  - `revoke_source` không chặn resolve đang chạy; ngược lại, thu hồi theo tác vụ chặn cả refresh hợp lệ sau lần đăng nhập mới (giờ dùng mốc theo nguồn và `began`);
  - link còn trong RAM sau khi file đã tải xong;
  - `finished_media` đứng sau việc tìm provider (PROVIDER_MISSING làm mất file đã kiểm);
  - `input` bị xóa giữa lúc đổi tên thành PUBLISH_FAILED;
  - trường log của FilePlan tham gia phép so sánh (2 test cũ hỏng);
  - danh sách số vé E2E/crossing phải đổi;
  - test F12 phụ thuộc thời gian thật (giờ dùng cache đồng hồ đứng).
  Mỗi sửa có test. Đột biến tay: 8 đột biến, cả 10 test nhắm tới đều FAIL.
- **Review code và review bảo mật sau khi code xong (hai agent chỉ đọc, không chạy test):** 0 CRITICAL/HIGH/MEDIUM; review code kết luận APPROVE.
  - LOW đã sửa (mỗi sửa có test):
    - link mới lấy trong lúc phiên đổi vẫn được dùng cho transfer (`_fresh_kept`);
    - link của phiên hết hạn hay bị từ chối chỉ rời bộ nhớ ở lần dùng sau;
    - hook bỏ link không chạy khi Ngắt kết nối lỗi giữa chừng;
    - link thăm dò chờ lâu vẫn dùng không kiểm;
    - Tiếp tục khi chưa có `input` kiểm lại cả file;
    - thông báo NO_INPUT_DIR hứa "không tải lại" cả cho yt-dlp;
    - chưa log loại dấu lúc thăm dò;
    - test race Hủy chưa đi qua đường token;
    - chỗ bỏ link khi dọn file tạm và quá 7 ngày chưa có test;
    - một assert yếu trong test đồng thời.
  - Đột biến vòng 2: 10 đột biến, cả 13 lần chạy test nhắm tới đều FAIL.
  - LOW giữ nguyên hoặc chỉ ghi lại:
    - bỏ link khi "chọn" chỉ là phòng thủ (ở NEEDS_CHOICE chưa có vé nên không có entry; không viết được test FAIL);
    - chữ "phiên bản file" trong thông báo Dừng chung cho cả yt-dlp;
    - tài liệu chưa commit có đường dẫn cá nhân tới prompt ngoài repo (bảo mật L4): đã bỏ sau review của Codex, tài liệu chỉ ghi tên file hoặc "ghi chú riêng bên ngoài repo".
- **Số liệu từ fixture** (vé là lượt ẩn + POST cấp vé; trước đây mỗi lượt tải lấy vé mới bằng một lượt ẩn khoảng nửa phút):
  - Thăm dò → tải xong: trước 2 vé (thăm dò + lấy lại nguồn) và 2 lần đọc mẫu 1 MiB; nay **1 POST cấp vé** (`RealProviderTest`, provider thật, chỉ lượt ẩn là giả) và 1 lần đọc mẫu.
  - Dừng → Tiếp tục với link còn giữ: **0 POST**, 1 lần đọc mẫu 1 MiB, rồi một request Range từ byte đã có kèm If-Range; 0 byte phải so lại. Trước đây: 1 vé mới, 1 lần đọc mẫu, và với host đổi ETag theo vé thì cả phần được nhận lại để so.
  - Link giữ trả dấu khác: 0 POST; cả phần (≥ 1,5 MiB trong fixture) được so từ byte 0 rồi mới nối. Khác byte ngoài 1 MiB cuối: không trộn, bản mới được tải nguyên.
  - Link giữ bị 401/403/404: đúng 1 vé thêm. NETWORK lúc kiểm: 0 vé, lần Tiếp tục sau vẫn 0 vé. 503: 0 vé lúc đó, lần sau 1 vé.
  - NO_INPUT_DIR → tạo `input` → Tiếp tục: 0 request tới máy chủ, 0 vé.
  - E2E/crossing (Edge thật, fixture): mỗi tập 2 → 1 vé; tập Dừng/Tiếp tục cả nhóm 3 → 1; F12 (đứt mạng rồi Tiếp tục) 3 → 1 với đúng 1 lần đọc mẫu khi Tiếp tục; R55 4 → 2 lượt ẩn.
- **Test mới/đổi:**
  - `tests/test_download_account_tickets.py` (22): ràng buộc, giới hạn (gồm cửa sổ 120 giây), đồng hồ monotonic, race thu hồi/ghi, mốc theo nguồn, repr.
  - `tests/test_download_account_reuse.py` (50, worker thật, root tạm, đồng hồ giả, canary trong link): vé đầu, probe đã chờ lâu, Dừng/Tiếp tục, dấu khác/khác byte/chỉ Last-Modified/không dấu, 401/403/404/NETWORK/503, refresh mới nhất, Thử lại/Hủy/Xóa, Ngắt kết nối/đăng nhập mới/hết phiên, các race của phiên, bí mật, NO_INPUT_DIR (gồm nguồn bị bỏ khỏi config, `input` mất giữa lúc đổi tên, file bị thay), log loại dấu, và `RealProviderTest` đếm POST.
  - `tests/test_download_account_api.py` +3: hook bỏ link (đăng nhập mới, Ngắt kết nối, Ngắt kết nối lỗi giữa chừng), hook lỗi không làm hỏng Ngắt kết nối.
  - `tests/test_download_worker_robustness.py` +1: yt-dlp gặp NO_INPUT_DIR rồi Tiếp tục với byte mới vẫn hoàn tất (so SHA-256 chỉ áp cho file của provider).
  - Fixture server: If-Range khớp cả Last-Modified.
  - E2E/crossing: số vé, lượt ẩn và request file theo hành vi mới.
- **Kết quả trên code cuối (worktree tính năng):**
  - Regression của Codex `download_account_post_test_review_tests.py` chạy nguyên trạng (SHA-256 `c02ab154…03bce4`): 3/3.
  - Test tập trung: `test_download_account_reuse` + `test_download_account_tickets` + `test_download_account_api` 88 đạt; `PublishTests` 4 đạt.
  - E2E và crossing (Edge thật, `BILIFLOW_REQUIRE_E2E=1`): 12/12 đạt, 0 skip, 487 s. Lượt này chạy trước các sửa sau review; full suite dưới đây chạy lại chúng trên code cuối.
  - JS gate 38/37/22/55/32.
  - Full suite: `unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, 22:15–22:54. Kết quả: **2.658 test trong 2.284,4 s, OK, 0 FAIL/ERROR**, 35 skip.
  - Lượt full suite quên đặt `BILIFLOW_FFMPEG`, nên 7 test tải video cần FFmpeg và 2 test khác bị skip. Chạy lại 7 module đó với FFmpeg của dự án: 106 test đạt; còn 12 skip (11 "project FFmpeg is required", 1 bộ phân loại an toàn), cùng lý do như các lần trước. Không còn skip nào trong test tải video hay tài khoản.
  - Không tăng timeout, không thêm skip. Test chỉ dùng fixture và root tạm, không đọc kho phiên thật, không lấy vé, probe hay media thật nào.
- **Giới hạn còn lại:**
  - Kiểm link khi Tiếp tục vẫn tốn một GET 1 MiB.
  - Host đổi dấu theo vé nên mỗi lần cache miss (quá 10 phút, restart, 401/403) vẫn phải so cả phần.
  - Một lượt ẩn đang chạy khi phiên đổi vẫn có thể lấy vé. Vé đó không được giữ hay dùng, nhưng POST đã gửi.
  - Chưa đo trên nguồn thật (không tiêu vé thật để đo).
  - Tác vụ yt-dlp gặp NO_INPUT_DIR: Tiếp tục chạy lại yt-dlp và chờ chỗ trống như một lượt tải; chỉ có kiểm tra đầy đủ, không so SHA-256 với lần trước.

- **Chép vào bản tích hợp (2026-10-10 đêm, sau review đạt của Codex):**
  - 19 file code/test giống hệt worktree tính năng: 3 file mới (`download_account_tickets.py`, `test_download_account_tickets.py`, `test_download_account_reuse.py`) và 16 file mà bản ở đây vẫn đúng nội dung trước §9.22 của worktree tính năng (kiểm bằng hash trước khi ghi), nên không thay đổi riêng nào của bản tích hợp bị đè. Không chép `AGENTS.md`, docstring ngoại lệ A, config, DB, vault hay root thử. Bản cũ lưu ở scratchpad của phiên (`reuse-port\backup-integ`). Tài liệu gộp tay.
  - Đường dẫn tuyệt đối cá nhân trong tài liệu được thay bằng tên file (điều kiện 1 của review Codex).
  - **Kết quả trên bản tích hợp:** tickets/reuse/api 88 đạt; regression Codex `download_account_post_test_review_tests.py` 3/3, chạy nguyên trạng (SHA-256 trước và sau như nhau); JS gate 38/37/22/55/32; `git diff --check` sạch; E2E và crossing (Edge thật, `BILIFLOW_REQUIRE_E2E=1`, FFmpeg dự án) 12/12, 0 skip, trong lượt chạy 7 module `test_download*` đầu (117 test đạt). Theo yêu cầu người dùng, phần còn lại của lượt đó dừng ở module kế tiếp: mọi file tải video khác của bản này cũng giống hệt worktree tính năng (bốn file tài khoản chỉ khác docstring ngoại lệ A; ba file gore là của main), nơi full suite đã đạt (2.658 test, 0 FAIL/ERROR), và review Codex không yêu cầu chạy lại full suite.
  - **Instance thử:** khởi động lại lúc 23:38 trên bản này. Tạo trước thư mục `input\` trống trong root; không đổi gì khác trong root. Instance cũ (server 30256, launcher 26404) đang rảnh (0 tác vụ, nhóm, lượt đăng nhập) và được dừng qua `/api/shutdown` của chính nó. Chạy lại ẩn với cùng root, cổng 8797 và `--no-import-existing`, `src` của checkout này đứng trước: launcher PID 5832, server PID 5868, khởi động 23:38:38, sau lúc chép code 23:27. Các file `download-core.js`, `download-view.js`, `download-live.js`, `download-episodes.js`, `app.js` đang phục vụ khớp từng byte với checkout. Đọc bằng GET: 0 tác vụ, nguồn NEEDS_LOGIN (SESSION_EXPIRED: lần đăng nhập trước đã quá mốc 1 giờ), người dùng đăng nhập lại. Chỉ instance này được khởi động lại; bản chính không bị sửa hay khởi động.
- **Lần thử thật của người dùng trên bản tích hợp (lượt 6, 2026-10-10 23:44–23:48, tác vụ 7, lượt tải 1):** người dùng tự đăng nhập lại (23:44), dán link và tự Dừng/Tiếp tục hai lần; agent chỉ đọc kết quả bằng GET (đã che tên, link và host).
  - Vé: cả tác vụ đúng **1** lượt ẩn có vé (34 s, 1 POST cấp vé) cho thăm dò và lượt tải đầu ("Dùng link tải vừa lấy lúc thăm dò…"). Code trước cần 4 lượt (thăm dò, lượt tải đầu, hai lần Tiếp tục).
  - Hai lần Dừng/Tiếp tục (23:45→23:46, 23:47→23:47): mỗi lần chỉ kiểm link đã giữ bằng một GET 1 MiB, **0 vé**, lượt tải bắt đầu ngay.
  - Dấu phiên bản: mọi câu trả lời có ETag mạnh và Last-Modified, nhưng ETag của cùng link đổi giữa các lần hỏi cách nhau khoảng một phút (dấu nhận sau lần so thứ nhất đã khác ở lần Tiếp tục sau). Vì vậy cả hai lần đi đường an toàn: phần đã tải (806.206.146 rồi 1.753.561.757 byte) được nhận lại từ byte 0, khớp từng byte rồi mới nối. Log chỉ ghi loại dấu nên chưa biết nguyên nhân (máy chủ đổi thời điểm sửa file, hay nhiều máy chủ phía sau). Không mở vòng tối ưu mới.
  - Kết thúc: COMPLETED 23:48:47; một file MKV 3.012.561.637 byte (HEVC + AAC, 3840×1608, 1:35:37) trong `input\` của root thử; thư mục tạm của tác vụ đã dọn. Watcher tạo job 1 NEEDS_METADATA, không tự quét. Người dùng tự xem: hình và âm thanh ổn.
  - Kết luận: đạt điều kiện nghiệm thu của prompt tích hợp (một POST cho thăm dò và lượt tải đầu, Dừng/Tiếp tục không lấy vé mới, đường an toàn khi dấu đổi, COMPLETED vào `input\`, hình và âm thanh ổn). Sẵn sàng cho Codex review; merge là bước riêng khi người dùng yêu cầu. Chưa commit/merge/push; bản chính không đổi.

## 10. Nhật ký triển khai

- 2026-10-07: tạo branch/worktree từ e8aea11; chuẩn bị kế hoạch/prompt; chưa implement runtime, chưa commit/merge/push.
- 2026-10-07: M0.
  - Đã đọc AGENTS, handoff, status, README và changelog.
  - Đã đối chiếu code (mục 9).
  - Đã cập nhật phạm vi trong `AGENTS.md` và `docs/VIDEO_DOWNLOAD_PLAN.md`.
  - Chỉ có tài liệu; chưa commit, merge hay push. Không đụng thư mục chính hay Control Center thật.
- 2026-10-07: M1.
  - Chỉnh tài liệu theo review M0 của Codex: 9.1 (FileTransfer nối Range không có If-Range khi thiếu validator; M3 không được nối mù), 9.6 (407 là lỗi xác thực proxy), 9.4 (không nới yêu cầu checked redirects và bounded reads cho proxy chưa kiểm chứng).
  - Thêm thư viện cấu hình, kho phiên, bảng tài khoản và session manager (mục 9.8).
  - Lượt test đầu: 55/55 OK; toàn bộ `test_download*` 508 OK (7 skip vì thiếu FFmpeg trong worktree, chạy lại riêng với `BILIFLOW_FFMPEG`: 7/7 OK); cache 17 OK.
  - Hai agent review code (bảo mật và Python, chỉ đọc): không có CRITICAL hay HIGH; 3 MEDIUM và các LOW đã sửa hoặc ghi vào giới hạn (mục 9.8). Thời hạn cửa sổ đăng nhập đặt lại 10 phút cho khớp kế hoạch (bản nháp để 15).
  - Sau khi sửa: 3 file mới 76/76 OK; toàn bộ `test_download*` 529 OK, không skip (có `BILIFLOW_FFMPEG`); cache 17 OK.
  - Không cài package, không chạy Control Center, không gọi API thật, không đọc hay giải mã phiên người dùng. Chưa commit, merge hay push.
- 2026-10-07: sửa lỗi P2 của review M1 (Codex).
  - Chạy file regression của Codex trước khi sửa: 1 FAIL, 2 ERROR, đúng như báo cáo.
  - Trạng thái tài khoản theo `(account_sid, source_id)`: bảng `source_account_state`, store theo một SID, SID lấy từ kho (token tiến trình), khóa theo root + SID + nguồn. Không chuyển dữ liệu; bảng nháp `source_accounts` nếu có thì để nguyên.
  - Test: 4 file của tính năng 86/86 OK; toàn bộ `test_download*` 539 test, OK, không skip, 161 giây; cache 17 test, OK. File gốc của Codex: 2/3 OK, test đầu chỉ khác mã mong đợi của B (mục 9.8).
  - Chỉ dùng SID giả, root tạm và DB giả; không mở DB hay phiên của bản chính. Không cài package, không chạy Control Center. Chưa commit, merge hay push. Chờ Codex review lại trước M2.
- 2026-10-07 → 2026-10-08: M2a.
  - Spike ngoài repo (`temp\m2a-spike`, `temp\m2a-smoke`) trên Edge 154 headless: cookie, redirect được fulfill, WebSocket, WebRTC, sandbox.
  - Chọn B (A không đạt yêu cầu kiểm từng redirect và giới hạn từng yêu cầu); thêm `download_account_http.py`, `download_account_browser.py`, helper profile của kho phiên và owner của lượt/lease (mục 9.9).
  - Hai agent review chỉ đọc, không có CRITICAL:
    - review bảo mật: 1 HIGH (Edge chạy `--no-sandbox`), 6 MEDIUM, 6 LOW;
    - review Python: 1 HIGH (bước chuyển tiếp không giới hạn), 4 MEDIUM, 8 LOW, nhiều điểm trùng review bảo mật.
    - Đã sửa hết, trừ các điểm ghi vào giới hạn ở 9.9: header theo danh sách loại trừ, handler chạy tuần tự, hai lượt cùng lease, `repr` của `Target`, mã hóa lại đường dẫn, PUT/PATCH/DELETE, trình quản lý mật khẩu (M2b).
  - Sau khi sửa, test trên Edge thật tìm thêm: Edge bỏ qua cờ WebRTC (chặn bằng tùy chọn profile); yêu cầu đầu của popup chưa có frame; SharedWorker và WebSocket của worker không qua route (dừng ở hố đen).
  - Một agent review lại các bản sửa (chỉ đọc): không có CRITICAL hay HIGH. Đã sửa hết các điểm mới:
    - N1: ghim profile trong lúc Edge chạy;
    - N2, N3: `navigate` chốt URL và status lúc tài liệu cuối vào khung, và chỉ khi khung mang đúng URL đó;
    - N4: redirect chỉ khác fragment được tải lại;
    - N5: hạn chung của `navigate` được kiểm cả trong handler;
    - N6: hủy được lúc đang tra DNS;
    - N7: `BrowserUnavailable` không giữ lỗi Playwright; chặn khởi chạy khi bật `DEBUG`/`PWDEBUG`;
    - N8: từ chối upload;
    - N9: không lưu phiên đã mất hết cookie;
    - L1: kiểm `launch_seconds`/`page_seconds`;
    - thêm test cho từng điểm.
  - Review lần ba các bản sửa (agent chỉ đọc): không có CRITICAL hay HIGH. Đã sửa các điểm LOW sau:
    - trang chuyển tiếp tự so URL trong Edge (trước đó so chuỗi trong Python, sai khi cách viết URL khác hay `#` rỗng);
    - `navigate` chỉ đổi fragment trả về ngay (trước đó chờ hết `page_seconds`);
    - ô file để trống không bị chặn;
    - đóng file ghim có bảo vệ lỗi;
    - thêm test vòng lặp redirect về chính URL có fragment.
  - Các điểm còn lại ghi vào giới hạn ở 9.9: sự kiện cùng tài liệu có thể làm `navigate` về sớm, yêu cầu xếp hàng sau khi `navigate` trả về, `_frames`, `open()` ngoài `with`.
  - Lượt cuối: HTTP 34, trình duyệt 55, kho phiên 29, M1 67, đều OK, không skip; toàn bộ `test_download*` 638 test OK, không skip; cache 17 OK.
  - Không cài package hay tải trình duyệt, không truy cập site thật, không mở hay giải mã phiên thật, không đọc cấu hình local, DB bản chính hay biên bản riêng, không chạy Control Center. Chưa commit, merge hay push. Chờ Codex review trước M2b.
- 2026-10-08: sửa P2 của review M2a (Codex).
  - Lỗi: `_comparable` chỉ so name, domain, path, value và expiry đã làm tròn. Server giữ nguyên giá trị cookie mà chỉ thêm HttpOnly và SameSite=Strict thì `run_with_session` không lưu, và lượt sau nạp lại thuộc tính cũ.
  - Chỉ đọc hai file review Codex chỉ định. Chạy regression độc lập của Codex trước khi sửa: 1 test, FAIL, lưu `(False, Lax)`, mong đợi `(True, Strict)`, đúng như báo cáo.
  - Sửa: so toàn bộ nội dung cookie và origin ở dạng chuẩn (mục 9.9, phần gắn kết quả). Không đổi `save_rotated`, generation, `authenticated_at` hay TTL.
  - Test mới:
    - unit: khác thứ tự không phải thay đổi; từng thuộc tính `httpOnly`, `secure`, `sameSite`, `partitionKey`, expiry lệch 0,25 giây và localStorage đều là thay đổi;
    - Edge thật: chỉ đổi thuộc tính thì được lưu, giữ generation, `authenticated_at` và trạng thái/TTL, lượt sau nạp đúng và gửi đúng cookie;
    - Edge thật: thay đổi thuộc tính của lease cũ (đăng nhập mới giữa lượt) và của manager khác (root khác, cùng nguồn và generation) bị từ chối;
    - Edge thật: phiên không đổi với cookie có expiry lẻ giây không bị lưu lại.
  - Sau khi sửa: regression của Codex 1/1 OK; trình duyệt 59 (47 trên Edge thật, 12 không cần trình duyệt), HTTP 34, kho phiên 29, M1 67, cache 17: 206 test OK, không skip; toàn bộ `test_download*` 642 test OK, không skip, 318 giây.
  - Chỉ dùng dữ liệu giả và root tạm. Không truy cập nguồn phim, không mở phiên thật, không chạy Control Center, không sửa thư mục chính, không cài gì. Chưa commit, merge hay push. Chờ Codex review lại trước M2b.
- 2026-10-08: yêu cầu bổ sung "dán trang phim và chọn tập".
  - Chỉ đọc đúng tài liệu yêu cầu người dùng chỉ định. Không chép tên miền, URL hay tên phim trong đó vào repo.
  - Cập nhật mục 1, 3 (khi dán link phim), 4D, 5 (M2b–M7), 7, 9.3, 9.7. Thêm mục 9.10: hành vi bắt buộc, ranh giới code hiện tại, thiết kế đề xuất, fixture, nghiệm thu và năm điểm cần chốt.
  - Đối chiếu code (chỉ đọc, base `e8aea11`): `add_tasks` chặn trùng theo URL; `MAX_UNFINISHED_TASKS`; `MAX_BATCH_LINKS`; NEEDS_CHOICE một mục; thứ tự hàng đợi; `sanitize_name`/`unique_target`; số slot.
  - Chưa có code cho phần này. P2 của M2a đã sửa ở mục trên; không đổi gì thêm ở code M2a. Chưa sang M2b. Chưa commit, merge hay push. Chờ Codex review.
- 2026-10-08: M2b (mục 9.11).
  - Chỉ đọc đúng file prompt M2b người dùng chỉ định. Kiểm `git worktree list`: không worktree nào khác có M2b; làm trong worktree này.
  - Thêm `download_account_login.py` và `tests/test_download_account_login.py`. Sửa `download_account_browser.py` (permit, `stop_at`, cửa sổ đóng, IndexedDB, tùy chọn mật khẩu và tự điền, lượt ẩn từ chối `headed`) và lời nhắn LOGIN_UNSUPPORTED trong `download_accounts.py`.
  - Đỏ trước, xanh sau (ghi lại trong lúc làm): Edge bỏ khóa `autofill.enabled` khi đóng (test đọc lại `Preferences` báo thiếu khóa); bỏ khóa đó, giữ `autofill.profile_enabled` và `credit_card_enabled` mà Edge giữ.
  - Một test hạn chung ban đầu đòi vòng lặp thấy hạn trước bộ hẹn giờ; khi cả bộ `test_download*` chạy thì bộ hẹn giờ có lúc đến trước (vẫn LOGIN_TIMEOUT). Sửa test cho nhận cả hai; trường hợp trang đang tải vẫn bắt buộc bộ hẹn giờ.
  - Lượt đầu: đăng nhập 24, trình duyệt 59, OK; toàn bộ `test_download*` 666 test, 665 OK, 1 FAIL là test hạn chung nói trên.
  - Một agent review bảo mật chỉ đọc: không có CRITICAL hay HIGH; 2 MEDIUM, nhiều LOW. Đã sửa:
    - M1: lời gọi Playwright treo (trang kẹt trong script, Edge treo) không bị hạn hay hủy dừng được, giữ LOGIN_BUSY tới khi khởi động lại. Nay Edge của đúng lượt bị kết thúc `grace_seconds` sau hạn hay sau khi hủy; có test trên Edge thật;
    - M2: Hủy gọi `cancel_login` theo nguồn, có thể kết thúc một lượt mới hơn bắt đầu ngay lúc đó. Nay Hủy kết thúc đúng lượt của cửa sổ;
    - LOW: `_finish` an toàn khi `end_login` lỗi; hạn chung tính từ ngay trước `begin_login`; `cancel` không ném lỗi DB; `shutdown` từ chối lượt mới và chờ trong một hạn tổng; kiểm hủy trước khi mở trang đăng nhập; `profile_left` và tên lớp lỗi trong `LoginOutcome`; `_END_CODES` lấy từ `LOGIN_END_CODES`; test quét `HeadedPermit(` cả thư mục con; test `LoginView` không cần Edge; test kết quả truthy không phải `True`, không tạo được luồng; sửa chú thích tùy chọn profile và docstring `start`.
    - Ghi vào giới hạn hoặc việc còn chờ (9.11): `storage_state` có thể mở trang nội bộ trong cửa sổ thật; `HeadedPermit` là quy ước; giới hạn 4 MiB với IndexedDB; bộ xác nhận có thể bị script của nguồn giả lời; tần suất hỏi; lượt ẩn chưa có cơ chế kết thúc Edge treo.
  - Kết quả sau khi sửa:
    - đăng nhập 35 (18 cửa sổ giả, 3 `LoginView` trên trang giả, 4 runtime Playwright giả, 10 trên Edge headless thật), OK; 21 test không cần trình duyệt chạy lại ba lần, đều OK;
    - trình duyệt 59 (M2a, gồm regression P2 về thuộc tính cookie), OK;
    - toàn bộ `test_download*` (có `BILIFLOW_FFMPEG`): 677 test, OK, không skip, 369 giây;
    - `tests.test_cache_dependencies` và `tests.test_stage_cache`: 17 OK.
  - `download_account_browser.py` nay 791 dòng, sát trần 800. M3 nên tách phần gắn kết quả (`run_with_session`, `_comparable`) hay phần Edge (`edge_processes`, `edge_environment`, `BlackHoleProxy`) ra file riêng trước khi thêm code.
  - Regression độc lập của Codex (file ngoài repo) không chạy lại ở lượt này, vì lượt này người dùng chỉ cho đọc file prompt M2b; test P2 tương ứng trong bộ trình duyệt vẫn đạt.
  - Sau test: không còn tiến trình Edge hay root tạm nào của test. Không cài package hay tải trình duyệt, không mở cửa sổ có giao diện, không truy cập nguồn phim, không mở phiên thật, không chạy Control Center, không sửa thư mục chính. Chưa commit, merge hay push. Chờ Codex review trước M3.
- 2026-10-08: sửa P2 của review M2b (Codex).
  - Chỉ đọc đúng ba file người dùng chỉ định: prompt sửa, báo cáo review, file test độc lập.
  - Lỗi: `_complete` đưa thẳng `storage_state()` vào `complete_login`, không kiểm lại điều kiện dừng sau khi đọc. Bộ hẹn giờ của hạn chung và `shutdown()` chỉ báo control, không kết thúc lượt trong manager, nên manager vẫn nhận commit. Hết hạn hay shutdown trong lúc đọc trạng thái: phiên vẫn được lưu và lượt trả CONNECTED.
  - Chạy hai test độc lập của Codex trước khi sửa: 2 FAIL (CONNECTED thay vì LOGIN_TIMEOUT và LOGIN_CANCELLED), đúng như báo cáo.
  - Sửa trong `download_account_login.py`:
    - `_complete` tách ba bước: thu thập, kiểm lại, commit; điểm commit là `complete_login`;
    - `_stop_run` cho Hủy, shutdown và hạn chung: báo control trước, rồi `end_login` đúng lượt của lượt đó dưới khóa của nguồn;
    - commit bị từ chối vì một lần dừng thì đọc là mã của lần dừng đó;
    - không lưu rồi đổi mã, không xóa phiên của lượt mới.
  - Test mới trong repo: 4 test điểm commit (cửa sổ giả, 13 trường hợp con) và 1 test trên Edge headless thật (2 trường hợp con).
    - Chạy với bản chép tạm ở scratch có logic cũ: 12 trường hợp con FAIL.
      - hạn chung hay shutdown lúc đọc trạng thái và ngay trước commit trả CONNECTED và lưu phiên;
      - Hủy và các trường hợp lượt mới không lưu gì, nhưng trả LOGIN_STALE thay vì mã của lần dừng.
    - Với bản sửa: đều OK.
  - Kết quả sau khi sửa:
    - hai test độc lập của Codex: 2/2 OK;
    - đăng nhập 40 (22 cửa sổ giả, 3 `LoginView`, 4 runtime Playwright giả, 11 trên Edge headless thật), OK;
    - toàn bộ `test_download*` (có `BILIFLOW_FFMPEG`; gồm trình duyệt, HTTP, kho phiên, M1): 682 test, OK, không skip, 384 giây;
    - `tests.test_cache_dependencies` và `tests.test_stage_cache`: 17 OK.
  - `docs/SESSION_HANDOFF.md`, `PROJECT_STATUS.md` và `CHANGELOG.md` chưa nhắc tính năng này; theo kế hoạch chúng được cập nhật ở M7, nên kế hoạch này vẫn là bàn giao của tính năng.
  - Chưa đăng nhập nguồn thật nào; registry bộ xác nhận nguồn thật vẫn trống (M3). Cửa sổ có giao diện thật vẫn chưa được người dùng kiểm. Không cài gì, không gọi API thật, không đọc cấu hình local hay DB bản chính, không mở cửa sổ đăng nhập thật, không tải phim, không chạy Control Center. Chưa commit, merge hay push. Chờ Codex review lại trước M3.
- 2026-10-08: M3 (mục 9.12).
  - Đọc AGENTS và các tài liệu đầu vào theo thứ tự, mục 9.1 và 9.8–9.11, code thật; đọc prompt M3 và `EPISODE-SELECTION.md` (được phép). Không đọc biên bản riêng khác, cấu hình local, DB hay phiên của bản chính.
  - Tách trước khi thêm code: `download_provider_config.py`, `download_account_edge.py`, `download_account_runs.py` (browser 791 → 620 dòng). Lượt đầu sau khi tách: 269 test của cấu hình, trình duyệt, đăng nhập, tài khoản, phạm vi, kho phiên, HTTP, dispatch: OK.
  - Thêm `download_account_listing.py`, `download_account_pages.py`, `download_account_sources.py`, `FilePlan.strict_versions`, `SourceLoginRequired`, provider tài khoản trong registry; fixture `tests/account_source_fixtures.py` và ba file test mới.
  - Lượt test đầu trên Edge: một FAIL do chính fixture (tab tự đóng sau 0,2 giây đóng trước khi được tìm thấy, nên đúng ra là TICKET_NOT_OPENED); sửa thời gian của trường hợp đó thành 1,5 giây. Trước lượt chạy đầu đã thêm kiểm tra hủy/hết hạn trong vòng chờ vé (đọc code thấy hủy lúc chờ vé chỉ dừng khi Edge bị kết thúc). Một kỳ vọng sai trong test thuần (mùa không số giữ chỗ của nguồn); sửa test, code đúng.
  - Toàn bộ `test_download*` cùng cache trước review: 765 test, OK, không skip, 643 giây.
  - Hai agent review chỉ đọc (đúng đắn và bảo mật): không có CRITICAL hay HIGH. Đã sửa:
    - vé chỉ khớp theo mã bản: nay phải đúng tập và bản (`TicketPage.episode`), fixture "other" là vé của tập khác cùng mã bản;
    - phim lẻ có danh sách chưa đủ từng được tự lấy bản duy nhất đã thấy: nay bắt chọn, nhãn ghi "danh sách bản chưa đủ"; trang không có file: NO_FILES;
    - cookie xoay vòng mất khi lượt kết thúc bằng lỗi, hủy hay hết hạn, và khi lỗi là HttpError: nay đọc và lưu trạng thái trước khi báo lỗi;
    - `login_url` có query thì mọi trang cùng path bị coi là trang đăng nhập: nay so cả query; link trang đã dán là trang đăng nhập bị từ chối;
    - trang sau của danh sách chuyển sang trang đăng nhập từng làm mất phiên: nay chỉ làm danh sách chưa đủ (PAGE_SIGNED_OUT); link trang chỉ trên cùng host, không bao giờ trang đăng nhập; nghỉ 0,3 giây giữa hai trang;
    - host files trùng portal hay tickets: cấu hình bị từ chối;
    - ValueError khi trang không có id phim, `sqlite3.Error` và AccountUnknown: nay là mã rõ (NOT_A_FILM_PAGE, PAGE_FAILED, ACCOUNT_STATE_ERROR); `mark_invalid` trả False (đăng nhập mới vừa tới) thì xin lại;
    - mã của `SourceLoginRequired` đổi thành SOURCE_LOGIN_REQUIRED, khác LOGIN_REQUIRED của 401 máy chủ file;
    - không bấm lấy vé khi lượt không còn đủ thời gian; trang vé lỡ hiện vé dưới 2 giây không phải lỗi; không quay vòng bận khi cả hai trang đã đóng; quảng cáo đóng ngay khi hiện host ngoài nguồn; trigger giới hạn 512 ký tự, fixture dựng trigger từ id đã kiểm;
    - `FileTransfer.run` tách hai helper (từ 72 còn khoảng 50 dòng);
    - import tên riêng giữa hai mô-đun đổi sang tên công khai.
    - Ghi vào giới hạn ở 9.12: không so dung lượng trang ghi, thứ tự mục không số với phân trang dạng cửa sổ, lưu cookie thất bại chưa báo, `navigation` trong `Listing`, chỗ tiêm của test, nguồn bị bỏ vì lỗi cấu hình, đọc trong main world.
  - Kết quả sau khi sửa:
    - `tests.test_download_account_sources` 35 (31 trên Edge headless thật), `test_download_account_listing` 33, `test_download_account_transfer` 8, cấu hình tài khoản 11 (thêm hai trường hợp con host trùng): OK;
    - toàn bộ `test_download*` (có `BILIFLOW_FFMPEG`; gồm trình duyệt, đăng nhập, HTTP, kho phiên, M1, provider, worker, API) cùng `tests.test_cache_dependencies` và `tests.test_stage_cache`: 775 test, OK, không skip, 668 giây.
  - `git diff --check` sạch; file mới và file sửa chỉ có host `.example`. Sau test: không còn tiến trình Edge hay root tạm nào của test.
  - Không cài package hay tải trình duyệt, không truy cập nguồn phim, không mở phiên thật, không gọi API thật, không chạy Control Center, không sửa thư mục chính. Registry bộ đọc trang và bộ xác nhận của nguồn thật vẫn trống. Chưa commit, merge hay push. Chờ Codex review trước M4.
- 2026-10-08: sửa sau review M3 của Codex (mục 9.13).
  - Chỉ đọc hai tài liệu kiểm tra được phép (bản review và file test độc lập). Chạy file test độc lập trước khi sửa: 4 FAIL.
  - Sửa ba lỗi:
    - runs: kiểm lại hủy và hạn sau khi trình duyệt đóng; thêm `FINISH_RESERVE_SECONDS` cho vé;
    - pages, listing: PAGE_NOT_FOLLOWED, PARTIAL_READ, `SelectionPlan.note`;
    - media file: so dấu phiên bản của 206 trước khi nối.
  - Test:
    - mới: `tests/test_download_account_runs.py`;
    - sửa kỳ vọng của test phân trang cũ;
    - thêm test listing, transfer và một ca Edge.
  - Kiểm ngược: vá tạm phần sửa trong bộ nhớ thì test mới FAIL (runs 6/7, transfer đúng 5 trường hợp khác bản).
  - Một agent review chỉ đọc: không có CRITICAL hay HIGH. Đã sửa ba LOW (test hủy dùng control đã hủy, `other_version` với Last-Modified không theo RFC, một comment cũ); hai LOW ghi vào giới hạn.
  - Kết quả:
    - 4 test độc lập OK;
    - lượt 1 toàn bộ 790 test OK (693 giây);
    - lượt 2: 789 OK và 1 FAIL do treo (test trang kẹt trong script); chạy riêng test đó 8/8 OK.

    Phát hiện treo được ghi ở 9.13, chưa sửa. Chỉ kết thúc node.exe của chính lượt test bị treo.
  - Không cài gì, không truy cập nguồn hay API thật, không đọc cấu hình, DB hay phiên của bản chính, không mở đăng nhập thật, không tải phim, không chạy Control Center, không sửa thư mục chính. Chưa commit, merge hay push. Chờ Codex review lại trước M4.
- 2026-10-08: Codex xác nhận ba lỗi trên đã sửa. Sửa lỗi driver Playwright treo sau khi Edge đóng theo prompt riêng (mục 9.13).
  - Chỉ đọc prompt sửa treo và Playwright 1.63 đã cài. Một spike xác minh nguyên nhân: driver tạm dừng giữ lời gọi đang chờ; kết thúc driver qua handle của nó thì lời gọi ném lỗi ngay và `stop()` trả về dưới 5 giây.
  - Sửa:
    - mới: `download_account_driver.py`;
    - `download_account_browser.py`: ghi driver, giai đoạn, `end_driver`, `hang`;
    - `download_account_runs.py` và `download_account_login.py`: bước thứ hai của watchdog, `hang` trên lỗi và kết quả.
  - Test:
    - mới: `tests/test_download_account_driver.py` (12) và `tests/test_download_account_hang.py` (8, Edge headless thật, gồm một lượt đăng nhập, watchdog ngoài 60 giây);
    - thêm 5 test trình duyệt giả trong runs, 2 trong login và 1 trong browser (fail closed).
  - Kiểm ngược: tắt bước kết thúc driver, hay bỏ nhánh bắt lời gọi được giải phóng của đăng nhập, trong bộ nhớ thì các test mới FAIL và vẫn dừng trong giới hạn.
  - Một agent review chỉ đọc: không có CRITICAL hay HIGH. Đã sửa M1, M2, M3, M5 và L1, L3, L6, L7, L8; M4, L2, L4, L9 ghi vào giới hạn (mục 9.13).
  - Kết quả: Codex M3 4/4 và M2b 2/2 OK; test tập trung 332 OK, không skip (673 giây); hang 8/8 (112 giây). Không còn tiến trình hay thư mục tạm của test.
  - Không cài gì, không truy cập nguồn hay API thật, không đọc cấu hình, DB hay phiên thật, không đăng nhập, không tải phim, không chạy Control Center, không sửa thư mục chính, không làm bộ đọc nguồn thật. Chỉ kết thúc tiến trình con của chính test. Chưa commit, merge hay push. Chờ Codex review trước M4.

- 2026-10-08: M4.
  - Chốt thiết kế ở mục 9.14 trước khi code; code theo đó.
  - Test mới: nhóm 24, hàng đợi 25, runtime/API 12 (61 OK). Toàn bộ suite 2.350 (28 ERROR chỉ vì thiếu `.mp4` trong `input\` của worktree; với clip tổng hợp 1 giây: 35/35 OK); sau các bản sửa `test_download*` 862 OK không skip, dashboard + cache 84 OK (1 skip), cache dependencies 13 OK, Node gate 37/22/32/38, Codex M3 4/4 và M2b 2/2.
  - Hai agent review chỉ đọc: không có CRITICAL hay HIGH; đã sửa 2 MEDIUM + 3 LOW của review code và 1 MEDIUM + 1 LOW của review bảo mật, phần còn lại ghi vào giới hạn (mục 9.14).
  - Không chạy Control Center thật, không đọc DB/cấu hình bản chính, không truy cập nguồn hay phiên thật, không cài package. Chưa commit, merge hay push.

- 2026-10-08: sửa sau review M4 của Codex (prompt sửa riêng ngoài repo; chỉ đọc bản review, prompt và file test độc lập).
  - Trước khi sửa: 3 test độc lập của Codex 3/3 FAIL.
  - Sửa (mục 9.15):
    - `download_account_tasks.py`: Hủy nhóm theo `CANCELLABLE`; lưu ý định trước khi đụng tác vụ; `_settle_groups`; từ chối Dừng/Tiếp tục/Thử lại nhóm đã hủy và Tiếp tục/Thử lại một tập của nó; không đánh thức tập của nhóm đã hủy.
    - `download_groups.py`: `hold`/`release` ghi `intent` cùng transaction; `cancel` xóa ý định cũ; `intents`, `clear_intent(s)`, `is_cancelled`, `cancelled_group_ids`, `cancelled_tasks`.
    - `download_store.py`: cột `download_group_members.intent`, index một phần, ALTER cho DB cũ.
    - `download_worker.py`: `dispatch` settle trước fill/đánh thức và bỏ qua tập của nhóm đã hủy; thao tác từng tập xóa ý định sau khi đổi trạng thái; `finished()`.
    - `download_upkeep.py`: `recover` settle ở cuối.
    - `download_api.py`: `stop` có giới hạn, đóng manager/store chỉ khi rảnh, luồng đóng hoãn idempotent, `close_when_idle`, `wait_closed`.
  - Test:
    - mới: `tests/test_download_group_intents.py` (23);
    - thêm 1 test hàng đợi thật (tập WAITING_LOGIN của nhóm bị ngắt khi hủy, restart, đăng nhập đến muộn: không đánh thức, không đọc trang, không xin vé);
    - đổi 3 test theo hợp đồng mới:
      - hai test Thử lại một tập nay dùng tập hủy riêng trong nhóm ACTIVE, vì tập của nhóm đã hủy bị từ chối; vẫn kiểm trùng tập và mức 100;
      - test "manager giữ mở khi cửa sổ còn chạy" chờ thêm lần đóng hoãn;
    - không đổi test nào của Codex.
  - Kiểm ngược: trả lại từng hành vi cũ trong bộ nhớ (tiêu chí hủy cũ, không settle, không từ chối, `stop` cũ, không chốt nhóm đã hủy) thì 14/14 test tương ứng FAIL.
  - Một agent review chỉ đọc: APPROVE, không CRITICAL/HIGH. Đã sửa:
    - 1 MEDIUM: thêm test cho chốt dispatch/đánh thức khi việc hủy lỗi, cho DB cũ và cho tác vụ đang chạy có ý định Dừng lúc crash;
    - 5 LOW: Thử lại nhóm không xóa ý định; lỗi đọc trong `_settle_groups` không chặn dispatch; Dừng nhóm đã hủy bị từ chối; sửa một comment; tách một dòng test dài.
  - Kết quả trên code cuối:
    - Codex M4 3/3, M3 4/4, M2b 2/2;
    - nhóm/hàng đợi/API tài khoản/ý định/store/robustness 114 OK;
    - toàn bộ suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree, watchdog ngoài 60 phút): 2.380 test trong 1.187 giây, 2.352 OK, 26 skip, 2 ERROR. Cả 2 ERROR ở `test_download_account_hang` (Edge headless thật, có từ M3, không đổi trong lượt này): lượt không treo phải xong trong hạn 8 giây mà Edge khởi động chậm khi cả suite cùng chạy; chạy riêng module đó 8/8 OK (113 giây). 26 skip: 11 thiếu FFmpeg của project trong worktree, 5 thiếu preview ident Golden, 4 thiếu bộ nhớ logo studio của project, 1 thùng rác thật (opt-in), 1 không có DB Control Center thử, 1 nhãn v1 không ở revision 469, 1 thiếu bộ phân loại an toàn đã cài, 1 thiếu Playwright cho kiểm trình duyệt điện thoại, 1 thiếu dữ liệu phim thật; không skip nào thuộc test tải.
    - Node gate `verify` 38/0, `verify-adapter` 37/0, `verify-download` 22/0, `verify-review` 32/0.
  - `test_download*` chạy trước các LOW của review: 882 test, 1 ERROR ở `test_download_worker.SpaceTests.test_waits_for_space_then_downloads` (test gốc, không đổi từ base). Đó là race có sẵn: test đọc thông báo "Chờ chỗ trống" ngay khi trạng thái đổi, trước lúc `_wait_for_space` ghi nó, trong khi agent review chạy test song song. Chạy riêng 10/10 OK. Đã gợi ý sửa ở phiên riêng; không sửa trong lượt này.
  - Không cài gì, không truy cập nguồn, phiên hay API thật, không đọc cấu hình/DB bản chính, không chạy hay restart Control Center thật, không tải phim, không sửa thư mục chính. Chưa commit, merge hay push. Chưa làm M5.

- 2026-10-08: giai đoạn A trước M5 (prompt gộp ổn định test và M5, ngoài repo; mục 9.16).
  - Hai ERROR của `test_download_account_hang` là lỗi của test: lượt bình thường phải xong trong deadline 8 giây dùng để cố ý treo. Bằng chứng: khi CPU bị chiếm hết, nạp phiên 8–10 giây và đóng Edge 18–38 giây; production cho 180 giây.
  - Sửa chỉ test:
    - lượt bình thường có hạn 45 giây, kèm kiểm timer deadline đã bị hủy;
    - lượt cố ý treo tính deadline 8 giây từ lúc driver ngừng trả lời, bằng bước dừng của chính lượt đó;
    - giữ một trường hợp deadline tính từ đầu lượt;
    - `SpaceTests` chờ đúng thông báo.
  - Kiểm ngược 3/3 FAIL. Một lỗi trong bản sửa đầu (lớp ghi lồng nhau ở test hai nguồn) đã sửa và thêm kiểm tra.
  - Kết quả:
    - module đã sửa 37 OK;
    - Codex M4 3/3, M3 4/4, M2b 2/2;
    - full suite trên code cuối 2.380 test, 1.206 giây, 2.353 OK, 26 skip, **1 ERROR**: `test_download_worker_robustness.SharedSpaceTests.test_a_second_download_counts_the_space_the_first_one_still_needs`, race có sẵn cùng kiểu (trễ 0,3 giây: 3/3 lỗi; nguyên bản 10/10 đạt), không sửa theo chỉ dẫn.
  - Chưa đạt cổng nên chưa làm M5; dừng để Codex kiểm tra.
- 2026-10-08: prompt bổ sung (ngoài repo) giao sửa race của `SharedSpaceTests` (mục 9.16, phần 3).
  - Trước sửa: script độc lập của Codex 3/3 tái hiện; sau sửa: `--expect-pass` 3/3, nguyên bản 10/10; kiểm ngược trong bộ nhớ 2/2 FAIL.
  - Chỉ sửa test: chờ WAITING_SPACE kèm thông báo; thêm kiểm lượt đầu vẫn tải và lượt hai chưa tải khi thiếu chỗ.
  - Code cuối: worker 44 OK; Codex M4 3/3, M3 4/4, M2b 2/2; full suite 2.380 test, 1.193,9 giây, 2.354 OK, 0 FAILURE/ERROR, 26 skip có lý do (log `full-suite-phaseA-last.log` trong scratchpad của phiên). Cổng A đạt.
  - Đã dọn 20 thư mục `temp\session-browser-*` của test và các tiến trình bằng chứng của phiên.
  - Không cài gì, không chạy Control Center thật, không truy cập nguồn/phiên thật, không sửa thư mục chính. Chưa commit, merge hay push.
- 2026-10-09: M5, giao diện Dashboard V2 (prompt M4-verify-and-M5 phần B, ngoài repo; mục 9.17).
  - Khung tài khoản nguồn phim, dòng lượt tải (chờ đăng nhập, Chọn tập, Xóa cả nhóm, tập trong nhóm), hộp chọn tập (nháp, khóa idempotency, `ITEMS_EXIST`) và mục Nhóm tập. Ngoài `dashboard_v2\` chỉ thêm `download-episodes.js` vào `control_center.DASHBOARD_V2_FILES`.
  - Ba agent review chỉ đọc (bảo mật, đối chiếu yêu cầu, JavaScript): không CRITICAL hay HIGH. Đã sửa mọi MEDIUM và phần lớn LOW; phần còn lại ghi vào giới hạn của mục 9.17.
  - Code cuối: gate node 38/37/22/29/32 (29 là gate mới); kiểm ngược 5/5 và 11/11 FAIL; test Python của Dashboard V2 và nhóm tập 157 test: 156 đạt + 1 skip; trình duyệt thật với server giả 78/78; full suite trên code cuối (`unittest discover`, clip tổng hợp 1 giây trong `input\` của worktree): 2.382 test trong 1205,9 giây, 2.356 OK, 0 FAILURE, 0 ERROR, 26 skip có lý do (log `m5-full.log` trong scratchpad của phiên). Không còn tiến trình nào của test; các server giả và Edge của kiểm tra trình duyệt đều đã dừng.
  - Không cài gì, không chạy Control Center thật, không truy cập nguồn/phiên thật, không sửa thư mục chính. Chưa commit, merge hay push. Dừng để Codex review.
- 2026-10-09: sửa sau review M5 của Codex (prompt M5-FIX, ngoài repo; mục 9.18).
  - P2: lỗi GET `/api/phone-mode` bị coi là đã biết chế độ, nên trang qua listener điện thoại hiện nút tài khoản PC. Nay chỉ `remote === false` mở các nút; chưa xác định thì có "Kiểm tra lại" (chỉ GET) và tối đa 5 lần tự hỏi lại. Store và bộ điều khiển đều từ chối POST; backend giữ nguyên.
  - Workflow review 4 agent chỉ đọc cộng kiểm chứng: không CRITICAL hay HIGH; đã sửa MEDIUM của test và các LOW trong phạm vi; ba điểm có từ trước ghi ở mục 9.18.
  - Code cuối: gate node 38/37/22/42/32; kiểm ngược 7 và 5 test FAIL trên code cũ, 6/6 đột biến FAIL; script của Codex `--expect-pass` đạt; trình duyệt 59/59; test Python focused 157 test: 156 đạt + 1 skip; full suite 2.382 test trong 1142,9 giây, 2.356 OK, 0 FAILURE, 0 ERROR, 26 skip có lý do (log `m5-full-2.log` trong scratchpad của phiên).
  - Không cài gì, không chạy Control Center thật, không truy cập nguồn/phiên thật, không sửa thư mục chính hay script của Codex. Chưa commit, merge hay push. Dừng để Codex review lại; chưa làm M6/M7.
- 2026-10-09: M6, kiểm tích hợp bằng fixture (prompt M6, ngoài repo; mục 9.19).
  - Ma trận yêu cầu → test; harness đầu-cuối `tests/account_e2e_fixtures.py`: E1–E7 qua Dashboard V2 trên Edge headless với handler, service, worker, store, runtime tài khoản và provider thật trên site fixture HTTPS; C1 với provider thật; thêm test hàng đợi và route.
  - 7 lỗi production sửa kèm test đỏ trước (mục 9.19): đếm làm mới vé khác bản; giới hạn `FILE_RETRIES` bị lách (có cả ở `main`), kèm hai sửa tiếp sau kiểm chứng đối kháng (200 của bản mới đưa mốc về 0 một lần mỗi lượt; validator của phần lấy từ câu trả lời bắt đầu ở byte 0); 2xx khác khi tải nối dừng BAD_RESPONSE (có cả ở `main`); race mất danh sách tập; số chờ đăng nhập của tài khoản Windows khác; thư mục tạm của trang; kiểm tra của "Tải các tập còn lại" và nhãn bản.
  - Test M6 có một race (đọc sự kiện ngay khi thấy trạng thái), đã sửa bằng `wait_event` và kiểm ngược với sự kiện ghi chậm 1 giây.
  - Review: bốn review chỉ đọc, workflow sửa 4 nhóm có kiểm chứng đối kháng, hai vòng kiểm chứng đối kháng cho hai bản sửa cuối, kiểm mô hình vét cạn và đột biến (xem mục 9.19).
  - Code cuối: E2E và crossing 12/12, 0 skip; gate node 38/37/22/54/32; script của Codex `--expect-pass` đạt; 12 module đường tải file 402 đạt; full suite (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree): 2.433 test trong 1.792,2 giây: 2.407 đạt + 26 skip, 0 FAILURE, 0 ERROR (log `m6-full-2.log` trong scratchpad của phiên).
  - Không cài gì, không chạy Control Center thật, không truy cập nguồn/phiên thật, không sửa thư mục chính hay script của Codex. Đã dừng các tiến trình và xóa các thư mục tạm do các lượt test của phiên tạo. Chưa commit, merge hay push. Dừng để Codex review; chưa làm M7.
- 2026-10-09 → 2026-10-10: sửa sau review M7 của Codex (prompt FIX-AND-TICKET, ngoài repo; mục 9.20).
  - Sửa P2 và chạy nguyên trạng script của Codex: 2/2 FAIL trước, 2/2 PASS sau, trên code cuối.
  - Code mới: nguồn gốc vé trong lượt, `ticket_page`, cookie host vé qua luồng của BiliFlow.
  - Ngoại lệ A làm trong chế độ quyền thủ công; người dùng duyệt từng lần sửa code. Đây là phương án thử Codex chọn, không phải rủi ro người dùng đã chấp nhận.
  - Đột biến tay: 16/16 mutant bị bắt sau khi thêm test. Review chỉ đọc: 0 CRITICAL/HIGH; MEDIUM đã sửa; 3 LOW ghi lại.
  - Full suite: 2.499 test; 1 FAIL và 1 ERROR do tải và khóa file của Windows; cả hai chạy lại riêng đều OK.
  - Config riêng: bản nháp nằm ngoài repo, mục files để trống.
  - `reads_tickets` vẫn False. Không truy cập nguồn thật, không vé, không probe.
  - Không cài gì, không chạy Control Center thật, không sửa thư mục chính hay script của Codex. Chưa commit, merge hay push. Dừng để Codex review. **M7 chưa hoàn tất.**
- 2026-10-10: final-verify M7 (review FIX-REVIEW và prompt FINAL-VERIFY, ngoài repo; mục 9.20 "Final-verify").
  - Chốt method POST của yêu cầu đầu: hai script Codex chạy nguyên trạng 2/2 mỗi script; đột biến bỏ chốt bị bắt.
  - Công cụ probe nghiệm thu `download_account_probe` + 19 test; 3/3 đột biến chốt của nó bị bắt; review chỉ đọc: HIGH (marker) và các MEDIUM/LOW đã sửa (marker ghi trong `before_click`).
  - Quan sát ẩn danh host vé (không thấy host file); instance thử root/cổng riêng; người dùng tự đăng nhập trong cửa sổ BiliFlow (CONNECTED); cổng với A đóng, trial click actionable, 0 POST; probe: phim bộ NEEDS_EPISODES, phim lẻ TICKET_UNSUPPORTED trước cú bấm; 0 byte media. Dừng trước mọi vé.
  - Đoạn đề xuất invariant Network cho A (chưa áp dụng).
  - Test: focused/E2E (provenance, listing, release_forms, sources, page_script, probe, browser, config, login, E2E, E2E flows, crossing; `BILIFLOW_REQUIRE_E2E=1`) trước bản sửa review: 281 test, 1.306,5 s, OK, 0 skip; sau bản sửa review, các module đã đổi (probe, provenance, listing, release_forms, sources): 145 test, 536,6 s, OK; hai script Codex trên code cuối: 2/2 mỗi script (sha256 không đổi); đột biến nhắm vào bản sửa review: 3/3 bị bắt; full suite trên code cuối, một lượt (`unittest discover`, `BILIFLOW_REQUIRE_E2E=1`, clip tổng hợp 1 giây trong `input\` của worktree), 2026-10-10 09:44–10:18: 2.523 test, 2.068,4 s, OK: 2.497 đạt + 26 skip, 0 FAIL, 0 ERROR (skip cùng lý do như trước; không có skip nào trong test tải video hay tài khoản; log `m7\logs\full-2.log` trong scratchpad của phiên). Lượt này thay cho lượt trước có 1 FAIL + 1 ERROR; hai lỗi đó không lặp lại.
  - Không cài gì, không chạy Control Center thật, không sửa thư mục chính hay script của Codex. Chưa commit, merge hay push. Dừng để Codex review. **M7 chưa hoàn tất.**
- 2026-10-10 chiều: quan sát một vé (prompt M7-OBSERVE-TICKET, ngoài repo; mục 9.20 "Quan sát một vé").
  - Chế độ quan sát riêng `download_account_observe` và chốt production `_check_submission`, kèm test fixture. Review chỉ đọc: 1 HIGH và 5 MEDIUM, đã sửa trước lượt thật.
  - Người dùng tự đăng nhập lại trong cửa sổ BiliFlow trên Control Center thử 8797 (CONNECTED). Control Center thử dừng trước lượt quan sát.
  - Lượt thật: 1 cú bấm, 1 POST vé, READY. Host vé lập phiên từ portal qua 6 chuyển hướng trong chuỗi của cú bấm. Đếm ngược 8 s. Host file trần chỉ lưu vào config riêng của root thử. Probe chính vé đó: 206, ETag mạnh, MKV HEVC/AAC, 1.048.576 byte media.
  - Docstring reader ghi hợp đồng đã đo. Fixture thêm `ticket_sso`, có test.
  - `reads_tickets` vẫn False: thay đổi bật nó bị bộ phân loại an toàn của phiên từ chối; chờ người dùng/Codex.
  - Test: hai script Codex 2/2 mỗi script, sha256 không đổi; observe/release_forms/probe 88 OK; full suite 2.551 test OK (2.525 đạt + 26 skip, 0 FAIL, 0 ERROR).
  - Không cài gì, không chạy Control Center thật, không sửa thư mục chính hay script của Codex. Chưa commit, merge hay push. Dừng để review trước merge/deploy.
- 2026-10-10 tối: bật reader và đóng M7 (review M7-READY và prompt M7-ENABLE-READER, ngoài repo; mục 9.20 "Bật reader và đóng M7").
  - Người dùng đồng ý; `reads_tickets = True` là một thay đổi riêng để người dùng duyệt thủ công, không bị từ chối lần này. Docstring ghi hợp đồng đã quan sát và giới hạn; mọi chốt giữ nguyên.
  - Test: `RegistryTest` (bật, chỉ đọc, adapter không reader vẫn unsupported); hai test reader tắt dùng `ListOnlyReader`; mới `ProductionReaderTest` dùng đúng reader đã đăng ký qua chuỗi SSO và đếm ngược; đột biến 2/2 bị bắt trên bản sao.
  - Test trên code cuối: 11 module bị tác động 245 OK (0 skip); hai script Codex 2/2 mỗi script, sha256 không đổi; full suite 2.553 test, 2.216,8 s, OK (2.527 đạt + 26 skip, 0 FAIL, 0 ERROR).
  - Không vé mới, không nguồn thật, không cài gì, không chạy Control Center thật, không sửa thư mục chính hay script của Codex. Chưa commit, merge hay push. **M7 đóng trong worktree**; dừng để Codex review trước khi đưa vào bản chính.
- 2026-10-10 chiều: bản tích hợp để test riêng (review M7-CLOSED và prompt INTEGRATION-TEST, ngoài repo). Người dùng đã chọn giữ ngoại lệ A ngày 2026-10-10; chưa cho triển khai bản chính.
  - Checkout detached `temp\wt-source-accounts-integration-test` tại `main` `99dff60`; áp patch thay đổi của nhánh so với base `e8aea11` (30 file tracked) và 57 file mới. Không commit, merge, rebase hay cherry-pick; bản chính và worktree tính năng không đổi.
  - Xung đột chỉ ở ba file trạng thái (cả hai bên chèn khối lên đầu): giữ chữ của main, khối của nhánh đặt phía trên. Các thay đổi gore C1 của main giữ nguyên từng byte.
  - `AGENTS.md`: invariant Network theo đề xuất §9.20 kèm quyết định giữ A của người dùng; dòng trạng thái source accounts (M0–M7, chỉ trong bản tích hợp). Docstring sửa theo thực tế: A theo lựa chọn của người dùng; chỉ lượt có thể lấy vé mới nạp script trang (trước ghi cả lượt đọc danh sách).
  - Kiểm trên bản tích hợp: cổng JS 38/37/22/54/32; kiểm reader 24 OK; kiểm tạm host file chính xác (TICKET_LINK_REFUSED, không request tới host file); hai script Codex 2/2 mỗi script; full suite 2.554 test, 2.208,7 s, OK (2.528 đạt + 26 skip, 0 FAIL, 0 ERROR).
  - Instance thử cổng 8797, root riêng trong `temp\` của checkout (chỉ config, không state/vault/marker), chờ người dùng tự đăng nhập. Chi tiết trong `SESSION_HANDOFF.md`.
- 2026-10-10 15:30–16:40: người dùng chạy thật trên bản tích hợp; sửa trong checkout tích hợp (worktree tính năng chưa có).
  - Lượt 1: lượt lấy vé lỗi BROWSER_FAILED, không có chi tiết. Sửa 1: báo bước và loại lỗi bằng từ cố định; giữ vé khi không đọc được state sau lượt (cảnh báo SESSION_STATE_UNREAD); lượt ẩn không tải ảnh/font của host nguồn; mỗi lượt một dòng nhật ký số liệu. Review chỉ đọc: không CRITICAL/HIGH.
  - Lượt 2: tải được; đọc danh sách 16 s (trước 46 s), lượt lấy vé ~33 s; đọc state hỏng `SESSION_READ_FAILED, INDEXEDDB` nhưng vé vẫn dùng.
  - Hai vấn đề người dùng báo: "hết phiên vẫn báo đã kết nối" (thật ra phiên còn hạn tới 16:29); "Dừng rồi Tiếp tục không tải" (mỗi lần Tiếp tục mất ~33 s lấy vé mà không báo gì, rồi file tải lại từ đầu vì ETag kiểu nginx `"<mtime>-<size>"` của host file khác nhau giữa các vé, phần size khớp file).
  - Sửa 2: tải nối qua vé mới bằng cách so 1 MiB cuối phần đã tải ngay trong câu trả lời (không thêm request, không If-Range; khác byte thì là phiên bản khác, đếm như cũ); trạng thái "Đang lấy link tải mới từ nguồn"; đọc lại state không kèm IndexedDB khi chỉ IndexedDB hỏng (giữ IndexedDB đã nạp).
  - Lượt 3 (16:46–16:48): Tiếp tục lần 1 tải nối qua vé mới (từ 498.793.157 tới 972.436.829 byte); lần 2 lỗi `SESSION_LOAD_FAILED, INDEXEDDB` (không phải hết phiên): state do sửa 2 lưu có thêm origin mới của trang vé mà Playwright không đọc rồi cũng không khôi phục được IndexedDB. Sửa 3: khi không đọc được IndexedDB chỉ lưu cookie mới và dữ liệu các origin đã nạp (bỏ origin mới); state không khôi phục được thì nạp lại không kèm IndexedDB, rồi chỉ cookie; dòng nhật ký ghi nguyên nhân IndexedDB bằng từ cố định. Sửa 4 (người dùng chọn): lỗi tạm của bộ đọc nguồn khi lấy link mới, trước hay trong lúc tải (BROWSER_FAILED, BROWSER_UNAVAILABLE, ACCOUNT_STATE_ERROR, TICKET_TIMEOUT), cho lượt về INTERRUPTED giữ phần đã tải (bấm Tiếp tục) thay vì FAILED (chỉ còn Thử lại từ byte 0). Sửa 5 (người dùng yêu cầu): quá mốc 1 giờ, panel hiện "Hết phiên" và "Phiên hết hạn lúc" theo đồng hồ của trang, không còn chữ "Đã kết nối". Các sửa được chép sang worktree tính năng để Codex review.
- 2026-10-10 18:40–20:00: review Codex (P1, P3) sửa ở worktree tính năng (mục 9.21 ở đó) rồi chép sang đây khi instance thử không có tác vụ: tải nối qua vé mới chỉ sau khi so mọi byte của phần từ byte 0; nhãn hết phiên giữ một chiều trên trang. Instance thử (chạy từ 17:13) vẫn chạy code Python cũ tới khi khởi động lại.
- 2026-10-10 20:18–20:40: Codex đóng P1/P3; chỉ instance thử 8797 khởi động lại (20:18) trên bản sửa. Người dùng tự thử (tác vụ 5, lần 2, một file MKV 7.132.741.612 byte): Dừng bốn lần và Tiếp tục sau mỗi lần, thêm một lần Tiếp tục sau khi trang không mở được (INTERRUPTED, NETWORK). Hai lần tải nối qua vé mới có validator khác: toàn bộ phần (2.360.998.599 và 3.647.568.546 byte) về lại từ byte 0, khớp từng byte rồi mới nối phần sau của cùng câu trả lời. Một lần (20:31) tải lại từ byte 0 vì phần tải bằng vé 20:30 không có validator (nhiều khả năng link đó không gửi ETag/Last-Modified; log chưa ghi loại dấu của từng link nên chưa chứng minh). File đạt kiểm tra (h264 + aac, 1920×1080, 8.035,2 s) và người dùng xác nhận file ổn. Tác vụ kết thúc FAILED NO_INPUT_DIR vì root thử không có `input\` (thiếu sót khi dựng môi trường thử, không phải lỗi tải). Chỉ đọc bằng GET và đọc DB thử ở chế độ read-only. Việc sau (người dùng chọn làm sau khi thử xong): (a) lần tải đầu dùng lại vé của lúc thăm dò/chọn bản; (b) giữ link ký của tác vụ đang chạy chỉ trong bộ nhớ để Dừng/Tiếp tục ngắn không cần vé mới và không phải so; ghi thêm: NO_INPUT_DIR có thể thành INTERRUPTED giữ file đã kiểm tra, và log loại validator của mỗi link mới (chỉ loại, không giá trị). Chưa commit, merge hay push; bản chính không bị sửa hay khởi động lại.
- 2026-10-10 21:00–23:00 (worktree tính năng): prompt TICKET-REUSE của Codex (§9.22): vé thăm dò dùng cho lượt tải đầu, giữ link trong RAM qua Dừng/Tiếp tục ngắn, NO_INPUT_DIR giữ file, log loại dấu. Ở đó: full suite 2.658 test OK, 0 FAIL/ERROR; E2E/crossing 12/12. Chờ Codex review.
- 2026-10-10 23:20–23:50: Codex review đạt; chép delta §9.22 vào bản tích hợp (19 file code/test giống worktree tính năng; tài liệu gộp tay; bỏ đường dẫn tuyệt đối cá nhân). Kiểm: tickets/reuse/api 88 đạt; regression Codex 3/3 (SHA-256 không đổi); JS gate 38/37/22/55/32; E2E/crossing 12/12 (0 skip) trong lượt chạy 7 module `test_download*` đầu (117 đạt); phần còn lại dừng theo yêu cầu người dùng vì code tải video giống hệt worktree tính năng, nơi full suite đã đạt. Tạo `input\` trống trong root thử; khởi động lại riêng instance 8797 lúc 23:38 (server 5868, launcher 5832). Chưa commit/merge/push; bản chính không đổi. Chờ người dùng tự thử.
- 2026-10-10 23:44–23:48: lượt 6, người dùng tự thử bản §9.22 trên instance 8797 (tác vụ 7): 1 lượt ẩn có vé cho thăm dò và lượt tải đầu; hai lần Dừng/Tiếp tục không vé mới (ETag của link đổi nên phần đã tải được so lại rồi mới nối); COMPLETED, file 3.012.561.637 byte trong `input\` của root thử; người dùng xác nhận hình và âm thanh ổn. Đạt; chờ Codex review, merge là bước riêng.
- 2026-10-11: Codex chốt nghiệm thu (lượt 6 đạt, không còn phát hiện mở). Người dùng cho commit, merge vào `main` và khởi động lại bản chính khi rảnh, không push; giữ ngoại lệ A. Ảnh chụp cuối của bản tích hợp được commit trên nhánh `release/source-accounts-20261011`. Kiểm trước commit: tickets/reuse/api 88 đạt; regression Codex 3/3, chạy nguyên trạng (SHA-256 không đổi); JS gate 38/37/22/55/32. Code đúng bản Codex đã nghiệm thu (không đổi code từ đó), nên E2E/crossing 12/12 của bản tích hợp và full suite của worktree tính năng vẫn là bằng chứng. Trước commit, quét bí mật trên các dòng staged thấy hai chuỗi mẫu trong `tests/test_download_account_observe.py` không được có trong repo public (một slug tên phim, một tên miền không phải ví dụ); đã đổi thành `ten-phim-mau` và `phim.example` (chỉ dữ liệu test), module đó đạt lại (26 test). Kết quả triển khai ghi ở mục sau khi xong.
- 2026-10-11 00:19–00:24: triển khai vào bản chính. Bản chính đã tắt sẵn (không có state, cổng 8765/8767/8797 trống), nên không dừng hay bật code cũ. Backup SQLite (backup API, nguồn chỉ đọc, integrity ok) và `config\*.local.json` vào `temp\deploy-backup-20261011-001907\`. `main` `99dff60` fast-forward lên `10ed81e`. Chép riêng `config\download_accounts.local.json` đã nghiệm thu (Git-ignored, đúng từng byte): validator của main đọc 1 nguồn, 0 lỗi; 3 host về đúng nguồn, không trùng host của 3 provider, không wildcard; `page_script` (ngoại lệ A) không phải host của nguồn. Không chép phiên, vault, cookie, DB, video, marker hay tác vụ của root thử. Khởi động `Start-BiliFlow.ps1 -NoBrowser` lúc 00:20:49: PID 29832, 127.0.0.1:8765, log lỗi rỗng. GET: healthz ok; 23 file Dashboard V2 phục vụ khớp từng byte; downloads 8 COMPLETED, không có lượt chạy, không lỗi; nguồn tài khoản NOT_CONNECTED (có đăng nhập và bộ đọc, 0 cảnh báo); chế độ điện thoại tắt như trước. Dữ liệu cũ giữ nguyên số dòng; migration thêm 4 bảng rỗng (`download_groups`, `download_group_members`, `download_previews`, `source_account_state`). Người dùng tự bấm Đăng nhập trên bản chính. Không push.
