# Mở BiliFlow trên điện thoại hoặc laptop (Wi-Fi nhà)

Chế độ này cho điện thoại hoặc laptop **cùng Wi-Fi nhà** mở Dashboard V2 của BiliFlow đang chạy trên PC. Mặc định chế độ này tắt.

> **Chỉ dùng trong Wi-Fi nhà.** Kết nối là HTTP, không mã hóa. Không bật khi PC đang dùng Wi-Fi công cộng (quán cà phê, khách sạn, sân bay).

## 1. Bật trên PC

1. Bấm đúp **`Start-BiliFlow-Phone.cmd`** trong thư mục BiliFlow (`E:\DungChung\BiliFlow`).
   - Control Center chưa chạy: file này mở Control Center như `Start-BiliFlow.cmd` rồi bật chế độ điện thoại.
   - Control Center đang chạy: file này **dùng lại** nó và chỉ bật chế độ điện thoại. Không khởi động lại, không mở Control Center thứ hai, job đang chạy không bị ảnh hưởng.
   - Control Center đang chạy bản cũ chưa có chế độ này: file báo rõ và **không** tự khởi động lại. Khi không có video nào đang xử lý, tắt bằng `Stop-BiliFlow.cmd` rồi chạy lại `Start-BiliFlow-Phone.cmd`.
2. Cửa sổ in ra:
   - `Mo tren dien thoai: http://192.168.x.x:8767/`: link để mở trên điện thoại;
   - `Nhap ma truy cap: xxxxxxxx`: mã 8 ký tự.

   Link không bao giờ kèm mã, để mã không nằm lại trong lịch sử trình duyệt của điện thoại.
3. Trình duyệt trên PC mở **Dashboard V2 → Cài đặt**. Khung **"Mở trên điện thoại"** hiện cùng link và mã, kèm nút **Tắt chế độ điện thoại**. Nút **Bật** cũng nằm ở đây.

## 2. Windows Firewall (chỉ làm một lần)

BiliFlow **không** tự sửa Firewall; bạn tự chọn một trong hai cách.

**Cách nên dùng: một rule riêng cho cổng 8767.** Chỉ mở cổng của chế độ điện thoại, chỉ cho mạng Private, chỉ cho máy trong cùng mạng.

1. Mở **PowerShell bằng quyền Administrator**: bấm Start, gõ `PowerShell`, chuột phải → *Run as administrator*.
2. Dán lệnh này rồi Enter:

```powershell
New-NetFirewallRule -DisplayName "BiliFlow phone mode (TCP 8767)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8767 -Profile Private -RemoteAddress LocalSubnet
```

3. Mạng Wi-Fi nhà phải đặt là **Private**: *Settings → Network & internet → Wi-Fi → (tên Wi-Fi) → Network profile type → Private network*.
4. Khi Windows hỏi có cho Python nhận kết nối không, có thể bấm **Cancel**, vì rule trên đã đủ.
   - Lưu ý: nếu trước đó đã bấm Cancel, Windows có thể đã tạo rule **Block** cho Python, và rule Block thắng rule Allow. Kiểm tra trong *Windows Defender Firewall → Advanced settings → Inbound Rules* (các rule tên `python` có biểu tượng chặn) rồi tắt rule đó.
5. Gỡ rule khi không dùng nữa:

```powershell
Remove-NetFirewallRule -DisplayName "BiliFlow phone mode (TCP 8767)"
```

**Cách dự phòng:** khi Windows hỏi cho Python qua tường lửa, tick **Private networks**, **không** tick Public. Cách này cho Python nhận kết nối ở mọi cổng trong mạng Private nên rộng hơn cách trên. Nếu lỡ bấm Cancel: *Windows Security → Firewall & network protection → Allow an app through firewall* → cho Python ở mục Private.

## 3. Mở trên điện thoại hoặc laptop

1. Điện thoại hoặc laptop phải nối **cùng Wi-Fi nhà** với PC.
2. Mở trình duyệt và vào link đã in ra, ví dụ `http://192.168.1.23:8767/`.
3. Gõ mã 8 ký tự vào ô **"Mã truy cập"** rồi bấm **Vào BiliFlow**. Chữ hoa hay chữ thường đều được.
4. Trình duyệt nhớ mã trong phiên. Tắt chế độ điện thoại thì phải nhập mã mới. Đóng hẳn trình duyệt cũng có thể phải nhập lại.

**Nhập sai mã 10 lần** thì việc nhập mã bị khóa. Có hai cách gỡ:

- **Khóa mở đặc biệt:** khi trang báo "Đã khóa nhập mã", gõ khóa mở đặc biệt vào ô **"Khóa mở đặc biệt"**. Khóa này do bạn chọn (câu 9 của kế hoạch). Nhập đúng thì khóa được gỡ và số lần sai về 0, **sau đó vẫn phải nhập mã 8 ký tự** trên PC. Khóa mở không tự cho vào BiliFlow.
  - Nhập sai khóa mở 5 lần thì khóa mở cũng bị khóa.
  - Mỗi lần bật chỉ gỡ được 3 lần; quá 3 lần cũng bị khóa.
  - Khi khóa mở đã bị khóa, chỉ còn cách tắt rồi bật lại trên PC.
- **Trên PC:** tắt rồi bật lại chế độ điện thoại để có mã mới; mọi bộ đếm về 0.

## 4. Trên điện thoại làm được gì

**Làm được:**
- xem tiến độ, hàng đợi, danh sách video;
- tạm dừng hoặc tiếp tục hàng đợi;
- thiết lập và bắt đầu quét, dừng, tiếp tục, thử lại, hủy, chạy lại;
- AI Audit dạng JSON (không gửi ảnh);
- mở trang duyệt cảnh, duyệt cảnh, xuất video.
  - **Duyệt cảnh qua điện thoại có thể thêm hoặc bỏ bản ghi logo đã nhớ**, giống trên PC: khi chọn quyết định có tick "nhớ logo hãng phim/nền tảng", hoặc khi đổi lại quyết định đã nhớ (câu 7). Chỉ **trang Bộ nhớ logo** (sửa/xóa trực tiếp) là chỉ-PC.
  - Trang duyệt khi mở trên điện thoại có thêm vài chỉnh nhỏ:
  - mũi tên **›** ở mép phải hàng bộ lọc (bấm để xem thêm);
  - không hiện chữ “phím 1–4”;
  - 4 nút **Giữ nguyên / Làm mờ / Cắt cảnh / Cần xem thêm** luôn nằm ở đáy màn hình;
  - chữ nhỏ được làm to hơn.

  Trang duyệt trên PC không đổi.

**Chỉ làm trên PC.** Trên điện thoại các nút này bị khóa, và nếu vẫn gửi lệnh thì server trả 403 kèm lý do:
- dọn, lưu trữ, khôi phục video gốc và kiểm tra lại Thùng rác;
- tắt Control Center;
- cấu hình và đăng nhập AI Supervisor;
- **Visual AI Audit** (gửi tối đa 36 ảnh thumbnail ra ngoài máy cho Codex);
- sửa hoặc xóa bộ nhớ logo;
- bật hoặc tắt chế độ điện thoại.

## 5. Tắt

- Trên PC: Dashboard V2 → Cài đặt → **Tắt chế độ điện thoại**. Listener đóng ngay và mã cũ hết hiệu lực.
- Hoặc `Stop-BiliFlow.cmd`: tắt Control Center, và chế độ điện thoại tắt theo.
- **Tự tắt:**
  - sau **8 giờ** kể từ lúc bật (khung trên PC hiện giờ sẽ tắt). Muốn dùng lâu hơn: bấm **Gia hạn thêm 8 giờ** trong khung trên PC; giờ tắt tính lại 8 giờ kể từ lúc bấm, mã giữ nguyên;
  - khi **địa chỉ Wi-Fi của PC đổi**, ví dụ đổi Wi-Fi hoặc mất mạng; kiểm tra mỗi phút.
- Khung "Mở trên điện thoại" cho biết lần tắt gần nhất vì sao: người dùng tắt, hết 8 giờ, đổi địa chỉ, hoặc Control Center dừng.
- Mỗi lần bật lại sẽ có mã mới.
- Khi chế độ đang bật, dashboard cũ (`/`) có dòng báo "Đang mở cho điện thoại: …". `Start-BiliFlow.cmd` cũng in dòng `NOTE: phone mode is ON` khi dùng lại Control Center đang bật chế độ này.
- **Một mã cho mọi thiết bị:** trong một lần bật, mọi thiết bị đã nhập đúng mã đều vào được. Muốn đuổi hết thiết bị: tắt rồi bật lại để có mã mới.

## 6. Nhật ký

Khung "Mở trên điện thoại" trên PC hiện khoảng 10 việc gần nhất: bật, tắt (kèm lý do), thiết bị nhập đúng mã, nhập sai mã, bị khóa, gỡ khóa bằng khóa mở, nhập sai khóa mở, khóa mở bị khóa. Mỗi dòng ghi **địa chỉ IP của thiết bị**, không bao giờ ghi mã, khóa mở hay cookie. Các việc này cũng được lưu vào nhật ký sự kiện của Control Center (`PHONE_*`). Gia hạn cũng được ghi.

Sau khi khởi động lại Control Center, khung đọc lại các việc gần nhất từ nhật ký sự kiện và cho biết lần tắt gần nhất vì sao. Nếu lần chạy trước dừng trong lúc chế độ đang bật, khung ghi "Control Center dừng".

## 7. Ghi chú kỹ thuật

- Listener riêng ở cổng **8767**. Nó chỉ nghe địa chỉ IPv4 riêng của PC (10.x, 172.16–31.x, 192.168.x), không bao giờ nghe `0.0.0.0` hay địa chỉ công cộng.
- `127.0.0.1:8765` trên PC giữ nguyên như cũ.
- Mọi request vào listener điện thoại đều cần cookie mã, trừ trang nhập mã:
  - cookie HttpOnly, SameSite=Strict;
  - giá trị cookie là HMAC của mã với một khóa ngẫu nhiên, không phải bản thân mã.
- Host phải đúng `ip:8767`, nhằm chống DNS rebinding.
- Lệnh ghi (POST) cần thêm token phiên và Origin đúng của listener. Listener điện thoại chỉ nhận các POST trong **danh sách cho phép** (`PHONE_ALLOWED_POSTS`); mọi POST khác, kể cả route thêm sau này, đều bị trả 403 `pc_only` trước khi đọc body.
- Chống quấy: tối đa 32 kết nối cùng lúc; kết nối chưa có cookie bị đóng sau 5 s; đường dẫn hỏng trả 400; log lỗi chỉ một dòng, tối đa 1 dòng mỗi 10 s.
- Bật/tắt chỉ nhận từ `127.0.0.1` kèm token phiên; bật/tắt không khởi động lại Control Center.
- Mã: 8 ký tự ngẫu nhiên, không gồm các ký tự dễ nhầm như `0/o`, `1/l/i`.
- Khóa mở đặc biệt (`UNLOCK_KEY` trong `src/biliflow/phone_access.py`) nằm trong mã nguồn, nên nó chỉ gỡ khóa, không bao giờ tự cấp cookie. Giới hạn: 5 lần sai, 3 lần gỡ mỗi lần bật.
