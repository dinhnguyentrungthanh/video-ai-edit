# Mở BiliFlow trên điện thoại hoặc laptop (Wi-Fi nhà)

Chế độ này cho điện thoại hoặc laptop **cùng Wi-Fi nhà** mở Dashboard V2 của BiliFlow đang chạy trên PC. Mặc định chế độ này tắt.

> **Chỉ dùng trong Wi-Fi nhà.** Kết nối là HTTP, không mã hóa. Không bật khi PC đang dùng Wi-Fi công cộng (quán cà phê, khách sạn, sân bay).

## 1. Bật trên PC

1. Bấm đúp **`Start-BiliFlow-Phone.cmd`** trong thư mục BiliFlow (`E:\DungChung\BiliFlow`).
   - Control Center chưa chạy: file này mở Control Center như `Start-BiliFlow.cmd` rồi bật chế độ điện thoại.
   - Control Center đang chạy: file này **dùng lại** nó và chỉ bật chế độ điện thoại. Không khởi động lại, không mở Control Center thứ hai, job đang chạy không bị ảnh hưởng.
   - Control Center đang chạy bản cũ chưa có chế độ này: file báo rõ và **không** tự khởi động lại. Khi không có video nào đang xử lý, tắt bằng `Stop-BiliFlow.cmd` rồi chạy lại `Start-BiliFlow-Phone.cmd`.
2. Cửa sổ in ra:
   - `Mo tren dien thoai: http://192.168.x.x:8767/?code=xxxxxxxx`: link mở thẳng, có sẵn mã;
   - `Hoac vao http://192.168.x.x:8767/ va nhap ma: xxxxxxxx`: link không kèm mã, và mã 8 ký tự.
3. Trình duyệt trên PC mở **Dashboard V2 → Cài đặt**. Khung **"Mở trên điện thoại"** hiện cùng link và mã, kèm nút **Tắt chế độ điện thoại**. Nút **Bật** cũng nằm ở đây.

## 2. Windows Firewall (chỉ lần đầu)

Lần đầu bật, Windows có thể hỏi có cho Python nhận kết nối không:
- Tick **Private networks** (mạng riêng / mạng nhà).
- **Không** tick Public networks.

BiliFlow không tự sửa Firewall. Nếu lỡ bấm Cancel thì điện thoại sẽ không vào được; mở *Windows Security → Firewall & network protection → Allow an app through firewall* rồi cho Python ở mục Private.

## 3. Mở trên điện thoại hoặc laptop

1. Điện thoại hoặc laptop phải nối **cùng Wi-Fi nhà** với PC.
2. Mở trình duyệt và vào link đã in ra.
   - Link có `?code=...`: vào thẳng.
   - Link không có mã: gõ mã 8 ký tự vào ô **"Mã truy cập"** rồi bấm **Vào BiliFlow**. Chữ hoa hay chữ thường đều được.
3. Trình duyệt nhớ mã trong phiên. Tắt chế độ điện thoại thì phải nhập mã mới. Đóng hẳn trình duyệt cũng có thể phải nhập lại.

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
- mở trang duyệt cảnh, duyệt cảnh, xuất video.

**Chỉ làm trên PC.** Trên điện thoại các nút này bị khóa, và nếu vẫn gửi lệnh thì server trả 403 kèm lý do:
- dọn, lưu trữ, khôi phục video gốc và kiểm tra lại Thùng rác;
- tắt Control Center;
- cấu hình và đăng nhập AI Supervisor;
- sửa hoặc xóa bộ nhớ logo;
- bật hoặc tắt chế độ điện thoại.

## 5. Tắt

- Trên PC: Dashboard V2 → Cài đặt → **Tắt chế độ điện thoại**. Listener đóng ngay và mã cũ hết hiệu lực.
- Hoặc `Stop-BiliFlow.cmd`: tắt Control Center, và chế độ điện thoại tắt theo.
- Mỗi lần bật lại sẽ có mã mới.

## 6. Ghi chú kỹ thuật

- Listener riêng ở cổng **8767**. Nó chỉ nghe địa chỉ IPv4 riêng của PC (10.x, 172.16–31.x, 192.168.x), không bao giờ nghe `0.0.0.0` hay địa chỉ công cộng.
- `127.0.0.1:8765` trên PC giữ nguyên như cũ.
- Mọi request vào listener điện thoại đều cần cookie mã, trừ trang nhập mã:
  - cookie HttpOnly, SameSite=Strict;
  - giá trị cookie là HMAC của mã với một khóa ngẫu nhiên, không phải bản thân mã.
- Host phải đúng `ip:8767`, nhằm chống DNS rebinding.
- Lệnh ghi (POST) cần thêm token phiên và Origin đúng của listener.
- Bật/tắt chỉ nhận từ `127.0.0.1` kèm token phiên; bật/tắt không khởi động lại Control Center.
- Mã: 8 ký tự ngẫu nhiên, không gồm các ký tự dễ nhầm như `0/o`, `1/l/i`.
- Khóa mở đặc biệt (`UNLOCK_KEY` trong `src/biliflow/phone_access.py`) nằm trong mã nguồn, nên nó chỉ gỡ khóa, không bao giờ tự cấp cookie. Giới hạn: 5 lần sai, 3 lần gỡ mỗi lần bật.
