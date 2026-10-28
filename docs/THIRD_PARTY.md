# Thành phần bên thứ ba

Phần mềm sử dụng thư viện mã nguồn mở theo giấy phép tương ứng. Không tuyên bố
nhóm viết Flask, Bootstrap, Chart.js, TensorFlow, OpenCV hoặc engine OCR.

| Thành phần | Vai trò | Tài liệu nguồn |
|---|---|---|
| Flask | Web backend | [Tài liệu](https://flask.palletsprojects.com/) |
| SQLAlchemy + pyodbc | Kết nối SQL Server và ORM | [Tài liệu](https://docs.sqlalchemy.org/en/20/dialects/mssql.html) |
| TensorFlow / Keras | Xây dựng và train CNN từ đầu | [Tài liệu](https://www.tensorflow.org/tutorials/images/classification) |
| OpenCV | Crop ROI, resize, tiền xử lý, hiển thị | [Tài liệu](https://docs.opencv.org/4.x/) |
| Google Gen AI SDK | Phân loại intent và diễn giải câu trả lời AI Chatbot | [Tài liệu](https://ai.google.dev/gemini-api/docs/text-generation) |
| Tesseract | OCR biển số phụ trợ bằng engine/trọng số có sẵn | [Tài liệu](https://tesseract-ocr.github.io/tessdoc/) |
| Bootstrap 5.3.3 | Thành phần giao diện | [Tài liệu](https://getbootstrap.com/) |
| Chart.js 4.4.7 | Biểu đồ thống kê | [Tài liệu](https://www.chartjs.org/docs/latest/) |

Bootstrap và Chart.js được phân phối trong `app/static/vendor` để giao diện chạy
local, kèm bản giấy phép `BOOTSTRAP_LICENSE.txt` và `CHARTJS_LICENSE.txt`.
Các thư viện Python được cài từ requirements, không đóng gói binary trong ZIP.

Mạng CNN, pipeline, luật nghiệp vụ và giao diện của dự án được xây dựng cho yêu
cầu này. Không sử dụng mô hình pretrained cho phân loại ô đỗ; OCR là thành phần
phụ trợ có sẵn và được ghi rõ trong README.
