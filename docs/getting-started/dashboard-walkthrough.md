# Panduan Pengguna — Tempa Dashboard (Langkah demi Langkah)

> Panduan ini menuntun Anda memakai **Tempa dashboard** dari nol: membuka dashboard,
> membuat workspace baru, mengunggah spesifikasi (PRD), menjalankan klarifikasi sampai
> bersih dari temuan kritikal/mayor, lalu menjalankan **Start Implementation** sampai
> aplikasinya selesai dibangun dan lulus QA — lengkap dengan tangkapan layar di setiap
> langkah. Ditujukan untuk pengguna yang baru pertama kali memakai Tempa.
>
> Latar belakang konsep (apa itu Tempa, mengapa dipakai) ada di [README.md](../../README.md)
> di root repo ini — panduan ini fokus ke **praktik**, klik demi klik, dengan contoh nyata:
> sebuah PRD sederhana ("Mortgage Installment Simulator") yang diproses dari awal sampai
> akhir.

## Daftar Isi

1. [Prasyarat](#1-prasyarat)
2. [Membuka Dashboard](#2-membuka-dashboard)
3. [Membuat Workspace Baru](#3-membuat-workspace-baru)
4. [Mengunggah Spesifikasi (PRD)](#4-mengunggah-spesifikasi-prd)
5. [Klarifikasi](#5-klarifikasi)
6. [Start Implementation](#6-start-implementation)
7. [Setelah Implementasi Selesai](#7-setelah-implementasi-selesai)
8. [Rujukan Lanjutan](#8-rujukan-lanjutan)

---

## 1. Prasyarat

Sebelum mulai, pastikan:

1. **Python 3** sudah terpasang dan bisa dipanggil dari terminal (`python`/`py`).
2. **Minimal satu CLI coding agentik** sudah terpasang, ada di `PATH`, dan **sudah login** —
   Tempa tidak menjalankan modelnya sendiri, ia menjalankan salah satu dari:
   - **Claude Code** (`claude`) — default,
   - **GitHub Copilot CLI** (`copilot`), atau
   - **OpenAI Codex CLI** (`codex`).
3. Tempa sendiri sudah diunduh/di-*clone* ke sebuah folder **di luar** proyek yang akan
   Anda kerjakan (lihat [README.md](../../README.md) bagian *Setup*).

Dashboard akan menampilkan status ketiga backend ini secara otomatis (✅/⬜) begitu Anda
membuka sebuah workspace — jadi Anda tidak perlu menebak-nebak mana yang sudah siap.

---

## 2. Membuka Dashboard

Dari folder instalasi Tempa, jalankan:

```bash
tempa dashboard            # jika folder Tempa sudah ditambahkan ke PATH
./tempa.cmd dashboard      # Windows, tanpa PATH
./tempa dashboard          # macOS/Linux, tanpa PATH
```

Perintah ini menjalankan sebuah server lokal dan mencetak alamatnya, misalnya:

```
Dashboard: http://127.0.0.1:51167/
Press Ctrl+C to stop.
```

lalu otomatis membuka alamat tersebut di browser Anda (biarkan jendela terminal ini tetap
terbuka — itu adalah server-nya; menutupnya akan mematikan dashboard). Jika belum pernah
membuka workspace apa pun sebelumnya, Anda akan melihat halaman **Home** seperti ini:

![Halaman Home sebelum ada workspace — tombol Select Working Folder dan Create New Working Folder](assets/01-home-no-workspace.png)

Di bawah kedua tombol itu ada daftar **Recent working folders** — workspace yang pernah
dibuka sebelumnya di komputer ini (pada tangkapan layar di atas, ini adalah mesin yang
sebelumnya sudah dipakai untuk proyek lain; kalau ini benar-benar pertama kalinya Anda
memakai Tempa, daftar ini akan kosong).

---

## 3. Membuat Workspace Baru

Ada dua tombol di halaman Home:

- **Select Working Folder** — memilih folder proyek yang **sudah ada** (misalnya repo yang
  sudah Anda mulai sebelumnya).
- **Create New Working Folder** — membuat folder proyek **baru dari nol**. Ini yang kita
  pakai di panduan ini.

Klik **Create New Working Folder**. Tempa akan:

1. Membuka dialog pemilihan folder bawaan sistem operasi Anda (Windows Explorer / Finder /
   `zenity`-`kdialog` di Linux) — pilih folder **induk** tempat proyek baru ini akan dibuat
   (misalnya `C:\work`).
2. Menampilkan kotak dialog kecil untuk mengetik **nama** folder proyek baru (misalnya
   `tempa-demo-mortgage-app`).
3. Membuat folder tersebut dan langsung menjalankan `tempa init` di dalamnya — menyiapkan
   struktur folder kerja standar (`docs/`, `src/`, `.tempa/` untuk data internal Tempa, dsb).

> Tidak ada dialog folder yang muncul? Di Linux, fitur ini butuh `zenity` atau `kdialog`.
> Tanpa keduanya, jalankan `tempa init <path-lengkap>` dari terminal, lalu muat ulang
> (refresh) halaman dashboard.

Setelah workspace dibuat, halaman Home berubah: sekarang menampilkan folder kerja yang
aktif, status kesiapan ketiga CLI backend, dan checklist 2 langkah berikutnya
(Upload Specification, Clarification):

![Halaman Home setelah workspace baru dibuat — menampilkan Working Folder, status CLI backend, dan langkah Upload Specification](assets/03-home-workspace-created.png)

Perhatikan panel **CLI backends ready for this workspace** — di sini ketiganya (Claude Code,
GitHub Copilot CLI, OpenAI Codex CLI) berstatus **ready**, artinya Tempa bisa memakai
salah satu (atau ketiganya sekaligus, berbeda tahap berbeda backend) untuk workspace ini.
Kalau salah satu belum ✅, itu tidak menghalangi — Anda cukup memastikan backend yang
dipakai pada tahap yang relevan (lihat Settings → AI Models) sudah siap.

---

## 4. Mengunggah Spesifikasi (PRD)

Spesifikasi (PRD — *Product Requirements Document*) adalah dokumen yang menjelaskan apa
yang ingin Anda bangun. Contoh yang dipakai di panduan ini adalah PRD sederhana untuk
**simulator angsuran KPR** (`examples/01-simple-web-app/PRD.md` di repo Tempa) — sebuah
aplikasi web sisi-klien murni tanpa backend.

Pada kartu **1. Upload Specification**, klik **Add File** lalu pilih file PRD Anda (bisa
lebih dari satu file/folder — Tempa membacanya semua sekaligus). Tempa menampilkan
konfirmasi sebelum menambahkannya:

![Dialog konfirmasi Add to Specification](assets/04-spec-uploaded.png)

Klik **Add**. File akan langsung muncul di sidebar kiri, di bawah **Specification**:

![Home setelah 1 file spesifikasi berhasil diunggah](assets/05-spec-added-home.png)

Klik nama filenya di sidebar (`PRD.md`) untuk membukanya:

![Sidebar Specification menampilkan PRD.md](assets/06-spec-sidebar.png)

Klik file tersebut untuk melihat isinya dalam tampilan **rendered Markdown** — heading,
bold, daftar bernomor semuanya dirender rapi, bukan teks mentah:

![PRD.md ditampilkan dalam mode View (rendered Markdown), dengan tombol View/Edit/Save di kanan atas](assets/07-prd-rendered.png)

Tombol **Edit** membuka editor teks biasa untuk mengubah isi file langsung dari browser
(lalu **Save** untuk menyimpan) — tidak perlu aplikasi editor terpisah. Anda bisa mengunggah
lebih dari satu file spesifikasi; Tempa akan membacanya sebagai satu kesatuan saat
klarifikasi dan implementasi berjalan.

> **Hanya unggah spesifikasi BARU** yang ingin diimplementasikan di sini. Dokumentasi sistem
> yang **sudah ada** (existing codebase) punya tempat terpisah (folder `docs/` proyek) supaya
> tidak tertukar dan tidak membuat Tempa membangun ulang sesuatu yang sudah jadi.

---

## 5. Klarifikasi

Ini tahap terpenting sebelum implementasi: Tempa **membaca ulang** PRD Anda dan mencari
bagian yang ambigu, tidak lengkap, atau saling bertentangan — sebelum satu baris kode pun
ditulis.

### 5.1 Menjalankan evaluasi pertama

Scroll ke kartu **2. Clarification**, lalu klik **Start Clarification**:

![Kartu Clarification pada halaman Home, tombol Start Clarification](assets/08-clarification-card.png)

Tempa langsung memanggil CLI backend yang dikonfigurasi untuk tahap Clarification (default:
Claude Code) untuk membaca seluruh PRD. Selama proses ini berjalan, tombol berubah menjadi
**Stop Now** dan indikator **Running…** muncul beserta durasi berjalan dan jumlah baris
temuan yang sedang ditulis:

![Klarifikasi sedang berjalan — status Running dengan durasi dan jumlah baris](assets/09-clarification-running.png)

### 5.2 Tab Log

Selama evaluasi berjalan (atau kapan pun), klik tab **Log** untuk melihat output mentah
proses tersebut secara langsung (live) — berguna untuk memastikan prosesnya benar-benar
berjalan, bukan macet:

![Tab Log Clarification menampilkan output konsol secara langsung](assets/10-clarification-log-tab.png)

Baris `log: nama_file.txt` yang tampil di sana **bisa diklik** — akan membuka isi file log
sesi tersebut dalam sebuah jendela pop-up, tanpa perlu mencarinya manual di folder
`.tempa/logs/`.

> **Kenapa halaman kadang perlu di-refresh?** Tempa membaca daftar file dari disk saat
> dashboard pertama kali dibuka, lalu meng-*cache*-nya. Kalau Anda menjalankan sesuatu lewat
> terminal secara bersamaan (jarang terjadi dalam pemakaian normal), klik tombol **Refresh**
> di kiri atas untuk memindai ulang folder kerja. Dalam pemakaian normal lewat dashboard saja,
> Anda tidak akan perlu melakukan ini — semua tombol run/save sudah otomatis menyegarkan
> tampilan.

Evaluasi pertama pada contoh kita butuh waktu sekitar 7 menit dan menghasilkan **7 temuan,
semuanya berseverity `critical`** (0 major, 0 minor) — ini normal untuk PRD yang belum
pernah diklarifikasi sama sekali. Perhatikan juga: Tempa **menyapu satu tingkat keparahan
dulu sampai tuntas** (critical → major → minor) alih-alih mengevaluasi ketiganya sekaligus,
supaya major/minor tidak dievaluasi terhadap PRD yang masih penuh isu mendasar. Klik tombol
**Refresh** di kiri atas untuk memuat ulang daftar file, lalu panel berubah menjadi:

![Overview Clarification menampilkan 1 file dengan 7 temuan critical, 0 dijawab](assets/11-clarification-overview-7critical.png)

### 5.3 Menjawab temuan

Klik nama file klarifikasi di sidebar (`clarification-20260826-212933.md`) untuk membuka
halaman jawab-temuan. Setiap temuan menampilkan: tingkat keparahan (**CRITICAL**/MAJOR/MINOR),
judul singkat, bagian **WHERE** (rujukan ke PRD), narasi masalah, **QUESTION**, dan
**RECOMMENDATION** dari agent:

![Tampilan satu temuan critical lengkap dengan Where, Question, dan Recommendation](assets/12-clarification-answer-ui.png)

#### Membuka rujukan PRD dari sebuah temuan

Perhatikan bagian **WHERE** — ada tautan biru seperti `PRD.md` dan `§2`. Klik salah satu
tautan bagian (misalnya **§2**) untuk membuka panel di sisi kanan yang menampilkan **bagian
persis** dari PRD yang dirujuk temuan tersebut, tersorot otomatis — tanpa harus membuka file
PRD secara terpisah dan mencari sendiri:

![Panel rujukan PRD terbuka di sisi kanan, menampilkan bagian "2. Usage Flow & UI" dari PRD.md](assets/13-clarification-prd-reference-drawer.png)

Panel ini bersifat *read-only* dan modal (halaman di baliknya berhenti merespons selagi
panel terbuka) — tutup dengan tombol **✕**, tombol **Esc**, atau klik di luar panel. Tombol
**⧉** membuka file itu di editor Specification penuh kalau Anda ingin mengeditnya.

Untuk setiap temuan, ada dua pilihan jawaban:

- **Follow the recommendation** — memakai jawaban/rekomendasi dari agent apa adanya.
- **I'll write my own answer** — membuka kotak teks untuk menulis keputusan Anda sendiri
  (dipakai kalau rekomendasi agent kurang sesuai dengan yang Anda inginkan).

![Memilih Follow the recommendation untuk temuan pertama](assets/14-clarification-follow-recommendation.png)

Contoh menulis jawaban sendiri untuk temuan kedua (menegaskan rumus anuitas yang dipakai):

![Memilih I'll write my own answer dan mengetik jawaban sendiri](assets/15-clarification-write-own-answer.png)

Daripada menjawab satu-satu, tombol **Follow all recommendations** di kanan atas langsung
menetapkan "Follow the recommendation" untuk **semua** temuan yang belum dijawab (jawaban
yang sudah Anda isi manual tetap dipertahankan):

![Follow all recommendations diklik — 5 temuan sisanya otomatis terisi](assets/16-clarification-follow-all.png)

### 5.4 Save vs Save & Clarify

Klik **Save** di kanan atas. Tempa menawarkan tiga pilihan:

![Dialog Save Answers dengan pilihan Cancel / Save & Clarify / Save](assets/17-clarification-save-dialog.png)

- **Save** — menyimpan jawaban ke file klarifikasi ini saja. Jawaban akan dibawa otomatis ke
  putaran evaluasi berikutnya (disebut *pending resolutions overlay*) **meskipun belum
  ditulis ke PRD**.
- **Save & Clarify** — sama seperti Save, lalu langsung menjalankan **Continue
  Clarification** (putaran evaluasi berikutnya) tanpa perlu kembali ke halaman overview.
- **Cancel** — batal, tidak menyimpan apa pun.

Penting: menjawab **tidak otomatis mengubah isi PRD**. Jawaban baru benar-benar ditulis ke
dalam dokumen PRD ketika Anda menekan **Apply Answers** (lihat 5.5). Sebelum di-*apply*,
jawaban itu tetap "diperhitungkan" oleh setiap evaluasi berikutnya — jadi Anda **tidak wajib**
apply di antara setiap putaran, cukup terus menjawab sampai bersih baru apply di akhir.

Kita pilih **Save & Clarify** supaya evaluasi putaran ke-2 langsung berjalan.

### 5.5 Mengulang sampai bersih dari critical

Setelah beberapa putaran, halaman Overview menunjukkan **Pending resolutions** — ringkasan
berapa banyak jawaban yang sudah tersimpan tapi belum ditulis ke PRD, dan tabel **Fully
answered** untuk putaran yang sudah selesai dijawab semuanya:

![Overview menampilkan kartu Pending resolutions (7 jawaban belum di-apply) dan riwayat putaran sebelumnya di tabel Fully answered](assets/18-clarification-pending-resolutions.png)

Teruskan pola yang sama — buka file, jawab (**Follow the recommendation**, tulis jawaban
sendiri, atau **Follow all recommendations** untuk sisanya), lalu **Save & Clarify** — sampai
jumlah **critical** pada hasil evaluasi menjadi **0**. Pada contoh PRD simulator KPR ini,
jumlah temuan critical per putaran berjalan seperti ini:

| Putaran | Critical ditemukan |
|---|---|
| 1 | 7 |
| 2 | 5 |
| 3 | 4 |
| 4 | 3 |
| 5 | 1 |
| 6 | 1 (temuan baru, bukan sisa) |
| 7 | 2 (temuan baru lagi) |

Ini **normal**: setiap putaran membaca ulang PRD + seluruh jawaban sebelumnya secara utuh,
jadi kadang muncul masalah baru yang baru "terlihat" setelah masalah lain dianggap
terselesaikan — bukan berarti putaran sebelumnya salah. Yang penting jumlahnya **menurun
secara umum**, bukan harus turun terus tanpa jeda di setiap putaran.

> **Tips:** Untuk 3–4 putaran pertama, sebaiknya jawab **manual** satu per satu, terutama
> untuk keputusan penting (tujuan aplikasi, alur bisnis, tech stack) — di putaran-putaran awal
> ini rekomendasi otomatis dari agent belum tentu sesuai dengan yang Anda maksud. Begitu
> rekomendasinya mulai konsisten selama 2 putaran terakhir (biasanya mulai putaran ke 4–5),
> Anda bisa beralih ke **Finalized Clarification** (5.6) supaya sisanya berjalan otomatis
> tanpa Anda tunggui satu-satu.

### 5.6 Finalized Clarification

Begitu evaluasi terakhir menunjukkan **0 critical finding** (major masih boleh ada), tombol
**Finalized Clarification** menjadi aktif dengan sendirinya. Tombol ini menjalankan loop
otomatis evaluate → jawab → evaluate sampai bersih, lalu satu **apply** dan satu **evaluasi
verifikasi** di akhir — tanpa Anda perlu mengklik apa pun lagi di antaranya.

Panel **Finalize readiness** menjelaskan persis syarat apa yang sudah/belum terpenuhi:

- Clarification pernah dijalankan minimal sekali.
- Hasil terakhir berasal dari **Start/Continue Clarification**, bukan cuma Apply Answers.
- Evaluasi terakhir menunjukkan 0 temuan critical.
- Backlog yang belum dijawab akan diisi otomatis dengan rekomendasinya sebelum loop mulai
  (baris ini informasi saja, bukan syarat yang harus Anda selesaikan lebih dulu).

#### Jalan pintas (opsional, untuk pengguna tingkat lanjut)

Pada contoh kita, critical sempat naik-turun di angka kecil (1–2) selama beberapa putaran
tanpa kunjung nol. Daripada terus menunggu manual, kita memakai opsi di
**Settings → Guardrails → Allow finalizing with critical findings**:

![Tab Settings → Guardrails, saklar Allow finalizing with critical findings dalam keadaan mati (default)](assets/19-settings-guardrails.png)

Mengaktifkannya menampilkan peringatan — **baca baik-baik sebelum menyalakan**, karena ini
membiarkan proses otomatis mencoba menyelesaikan temuan critical **tanpa pengawasan Anda**:

![Dialog konfirmasi peringatan saat mengaktifkan Allow finalizing with critical findings](assets/20-guardrail-warning-dialog.png)

Setelah dikonfirmasi dan **Save Settings** ditekan, kartu tersebut menampilkan status
aktif beserta peringatannya:

![Kartu Guardrails menampilkan status Enabled beserta peringatan risikonya](assets/21-guardrail-enabled-warning.png)

> **Kapan pantas dipakai:** hanya kalau Anda sudah menjawab beberapa putaran manual dan
> yakin sisa temuan critical yang berputar-putar itu bukan masalah fundamental (biasanya
> nuansa kecil yang saling terkait) — dan Anda berkomitmen **memeriksa ulang** hasil PRD
> setelah selesai. Untuk PRD baru yang belum pernah dijawab sama sekali, biarkan opsi ini
> **mati** (default) dan jawab manual seperti 5.3–5.5 di atas.

Kembali ke halaman Clarification — panel **Finalize readiness** sekarang menandai baris
critical dengan catatan **"allowed via the Settings override"**, dan tombol **Finalized
Clarification** aktif meski evaluasi terakhir masih menunjukkan temuan critical:

![Finalize readiness dengan catatan allowed via the Settings override, tombol Finalized Clarification aktif](assets/22-finalize-readiness-override.png)

Klik **Finalized Clarification**, lalu pindah ke tab **Log** untuk memantau jalannya —
proses ini bisa memakan beberapa putaran sekaligus (sampai batas **Max Finalize
Clarification Round**, default 20), jadi wajar kalau berjalan cukup lama:

![Finalized Clarification berjalan — tombol berubah jadi Stop Now, status Finalizing…](assets/23-clarification-finalize-running.png)

Finalize berhenti begitu **critical dan major sudah nol** (temuan minor boleh tersisa —
temuan itu tetap akan ditangani nanti saat implementasi). Kalau finalize selesai tapi
**masih ada temuan major**, itu artinya guardrail "no-progress" tercapai (agent berhenti
membuat kemajuan) — jalankan **Finalized Clarification** sekali lagi, atau jawab sisa major
itu secara manual seperti putaran-putaran sebelumnya, lalu jalankan Finalize lagi sampai
benar-benar bersih.

#### Mengatur seberapa sering PRD ditulis selama Finalize berjalan

Secara default, Finalize menyimpan semua jawaban di memori dan baru menuliskannya ke PRD
**sekali di akhir** proses. Untuk proses yang berjalan lama tanpa pengawasan, ini berarti
kalau terjadi sesuatu di tengah jalan, jam-jam kerja agent belum tersimpan ke dokumen sama
sekali. Settings → Runs → **Finalize Checkpoints** mengatur seberapa sering (setiap berapa
putaran menjawab) Tempa berhenti sejenak untuk **apply** (menulis yang sudah terjawab ke PRD)
dan (kalau diaktifkan) **commit** — jadi progres jangka panjang tetap punya titik pemulihan:

![Settings Runs, kartu Finalize Checkpoints dengan field Checkpoint Every N Rounds](assets/24-settings-finalize-checkpoint.png)

Selama sesi menyusun panduan ini, setelah beberapa putaran kami mengubah nilainya dari
default (3) menjadi **5** — jadi PRD ditulis ulang tiap 5 putaran-menjawab alih-alih 3.
Perubahan ini baru berlaku pada proses Finalized Clarification **berikutnya** (dibaca sekali
saat sebuah run dimulai), jadi kalau Anda mengubahnya di tengah proses yang sedang berjalan,
hentikan dulu (Stop After Current Round), ubah nilainya, baru klik Finalized Clarification
lagi untuk melanjutkan dengan interval yang baru. Kosongkan field-nya kalau Anda tidak ingin
checkpoint sama sekali (murni satu kali tulis di akhir, seperti perilaku sebelum fitur ini
ada).

### 5.7 Ketika klarifikasi tidak kunjung benar-benar bersih

Cerita nyata dari sesi yang dipakai untuk panduan ini: setelah masuk ke **Finalized
Clarification**, jumlah critical sempat mencapai nol lalu fasenya melebar ke **major
sweep** — tapi begitu major juga nol dan proses masuk tahap *compaction* (menulis ke PRD),
**evaluasi verifikasi setelah apply itu sendiri menemukan temuan critical/major yang baru**.
Ini persis seperti yang dijelaskan di 5.5: PRD yang baru saja diperbarui punya permukaan baru
yang belum pernah dicek. Finalize otomatis mengulang loop-nya (dibatasi maksimal 2
kali compaction per run), dan pada percobaan kami angkanya naik-turun di kisaran 0–2 critical
selama beberapa putaran lagi tanpa benar-benar berhenti di nol secara stabil.

**Ini adalah keputusan nyata yang mungkin juga Anda hadapi:** teruskan menunggu sampai
benar-benar konvergen (bisa memakan puluhan menit sampai berjam-jam tambahan), atau lanjutkan
ke implementasi dengan sisa temuan yang ada kalau Anda menilai temuan yang tersisa tidak
mengubah hal-hal penting yang akan diimplementasikan. Pada demo ini kami memilih opsi kedua.

#### Melonggarkan syarat Start Implementation

Secara default, Settings → Guardrails → **Start Implementation requires** diset ke **"No
critical or major findings"** — paling aman, tapi berarti Start Implementation tetap
terkunci selama masih ada sisa temuan. Untuk melanjutkan meski masih ada 1 critical
finding yang tersisa, kami mengubahnya ke **"No condition"**:

![Dialog konfirmasi saat melonggarkan Start Implementation requirement ke No condition](assets/25-relax-start-implementation-dialog.png)

> **Peringatan sama seperti opsi di 5.6**: melonggarkan syarat ini berarti implementasi bisa
> dimulai di atas spesifikasi yang berpotensi masih ambigu di beberapa bagian kecil. Hanya
> lakukan ini kalau Anda sudah meninjau sisa temuannya dan yakin itu tidak krusial untuk
> fitur yang akan dibangun. Untuk proyek sungguhan, opsi paling aman tetap: teruskan
> menjawab manual sampai benar-benar 0/0/0 sebelum lanjut ke implementasi.

Perlu diketahui: **melonggarkan syarat ini TIDAK menghapus kewajiban Apply Answers.**
Sekalipun syaratnya "No condition", Tempa tetap mewajibkan setiap jawaban yang sudah
tersimpan untuk ditulis ke PRD lebih dulu (baris "Pending resolutions" harus nol) —
supaya keputusan yang sudah Anda buat benar-benar terbaca oleh proses implementasi.

### 5.8 Apply Answers

Klik **Apply Answers** untuk menulis semua jawaban yang masih "mengambang" ke dalam
dokumen PRD (termasuk mengisi rekomendasi otomatis untuk finding yang belum sempat
dijawab sama sekali):

![Apply Answers sedang berjalan](assets/29-apply-answers-running.png)

Setelah selesai, kartu **Clarification** di Home menunjukkan ringkasan akhir — pada contoh
kita: "0 of 58 finding(s) not yet answered (1 critical). Finalizing is allowed anyway via
the Settings override." — dan tombol **Start Implementation** di Step 3 sudah aktif:

![Home Step 3 Start Implementation aktif, dengan catatan syarat yang dilonggarkan](assets/26-home-start-implementation-ready.png)

---

## 6. Start Implementation

Klik **Start Implementation**. Halaman berpindah ke bagian **Implementation**, tombol
berubah jadi **Stop Now** dengan status **Running…**, dan panel **Implementation readiness**
menunjukkan syarat apa yang dipakai (di contoh kita: critical/major diperbolehkan karena
requirement sudah dilonggarkan ke "No condition" pada 5.7):

![Implementation baru dimulai — panel readiness dan tab Log menampilkan proses plan drafting dimulai](assets/27-implementation-started.png)

### 6.1 Tahap pertama: Plan Drafting (otomatis)

Karena belum ada epic/feature sama sekali, Tempa **otomatis membuat rencana kerja lebih
dulu** sebelum menulis kode apa pun — mempelajari PRD (dan folder `docs/` proyek untuk tahu
apa yang sudah ada), lalu menyusun struktur **epic → feature → task**. Tab **Status**
menunjukkan ini sedang berjalan (belum ada epic untuk ditampilkan):

![Tab Status, masih menunjukkan "No plan/epic yet" saat plan drafting berjalan](assets/28-implementation-status-tab-planning.png)

Tab **Log** adalah tempat paling informatif untuk memantau proses ini — semua tahapan
(plan drafting, per-epic implementation, QA, fixing) mengalir sebagai satu log yang sama,
dibedakan lewat header `== ... ==` di setiap sesi baru.

### 6.2 Tab Status: memantau epic & feature

Begitu plan selesai dibuat, tab **Status** menampilkan daftar **epic** (dan feature di
dalamnya) beserta status masing-masing. Pada contoh kita, PRD simulator KPR dipecah
menjadi 4 epic (EPIC-01 s/d EPIC-04) berisi total 19 feature:

![Tab Status menampilkan daftar epic dan feature, semua masih Pending](assets/30-implementation-status-epics.png)

> Sebelum epic pertama benar-benar mulai dikerjakan, ada satu langkah tambahan yang mungkin
> tidak terlihat kalau Anda tidak membuka tab Log: **REVIEW-EPICS** — sesi terpisah yang
> meninjau ulang rencana yang baru dibuat (cakupan terhadap PRD, ukuran tiap feature,
> testability, potensi paralelisasi) dan memperbaikinya kalau perlu, sebelum implementasi
> feature pertama dimulai.

Begitu EPIC-01 mulai dikerjakan, statusnya berubah jadi **On_progress** dan checkbox di
tiap feature-nya mulai tercentang satu per satu seiring feature itu selesai:

![EPIC-01 berstatus On_progress, feature-feature-nya sedang dikerjakan satu per satu](assets/32-implementation-epic-in-progress.png)

Setelah **semua** feature dalam epic itu selesai, statusnya berubah jadi **Done** dan QA
otomatis langsung berjalan (badge **QA running** muncul di sebelah nama epic) — tanpa Anda
perlu memicu apa pun:

![EPIC-01 berstatus Done, 5/5 feature selesai, badge QA running muncul](assets/33-implementation-epic1-done.png)

Setiap **epic** melewati siklus status ini:

```
pending ──► on_progress ──► done ──►[QA]──► qa_passed=true ✅ (lanjut ke epic berikutnya)
                                      │
                                      └─(QA menemukan masalah)─► require_fixing ──► on_progress ──► ...
```

- **pending** — belum dikerjakan.
- **on_progress** — sedang diimplementasikan.
- **done** — semua feature di epic ini sudah selesai ditulis, menunggu giliran QA.
- **require_fixing** — sudah pernah diimplementasikan tapi QA menemukan masalah; akan
  diperbaiki lalu di-QA ulang.
- **failed** — sesi error sungguhan (bukan sekadar limit penggunaan AI habis, itu ditangani
  otomatis dengan menunggu). Perlu perhatian manual — lihat 6.7.
- **deferred** — epic ini menunggu **keputusan Anda** untuk satu atau lebih fitur di
  dalamnya (lihat 6.6), tapi epic lain tetap lanjut berjalan.

Klik salah satu epic untuk melihat rinciannya, termasuk **riwayat QA** (setiap putaran QA
yang pernah dijalankan untuk epic itu, temuan apa saja, dan tautan ke laporan lengkapnya).

### 6.3 Tab Log: mengikuti proses secara live

Tab **Log** menampilkan output konsol mentah — sama seperti pada Clarification, baris
`log: nama_file.txt` di sini juga bisa diklik untuk membuka isi file log sesi tersebut
(implementasi, QA, atau plan) dalam jendela pop-up:

![Tab Log Implementation menampilkan output sesi implementasi/QA secara live, dengan tautan log yang bisa diklik](assets/31-implementation-log-tab.png)

Klik salah satu nama file log berwarna biru (misalnya `qa_EPIC-04_...txt`) untuk membuka
isi lengkapnya dalam jendela pop-up — berguna kalau Anda ingin melihat persis apa yang
dikerjakan/diperiksa agent di satu sesi tertentu, tanpa harus membuka folder
`.tempa/logs/` secara manual:

![Jendela pop-up menampilkan isi lengkap satu file log QA, lengkap dengan tombol fullscreen dan tutup](assets/40-log-file-viewer-modal.png)

Tombol perbesar (kiri dari **✕**) membuka pop-up ini dalam mode layar penuh — berguna untuk
log yang panjang. Isi file log berisi transkrip mentah sesi tersebut: setiap tool yang
dipanggil agent (`Bash`, `Read`, `Edit`, dst.) beserta hasilnya, persis seperti yang akan
Anda lihat kalau membuka file `.txt`-nya langsung — termasuk info sesi seperti
`session_id` dan model yang dipakai di baris paling atas.

### 6.4 Alur QA dan perbaikan otomatis

Begitu **semua feature** dalam satu epic sudah ditandai selesai, Tempa **otomatis
menjalankan QA** terhadap epic itu — sesi terpisah yang memeriksa setiap feature terhadap
spesifikasinya, lalu memberi salah satu dari tiga label per feature:

- ❌ **Tidak diimplementasikan** / gagal saat benar-benar dijalankan — **memblokir** epic
  (jadi `require_fixing`).
- ⚠️ **Perilakunya berbeda dari spesifikasi** — juga **memblokir**.
- 📝 **Catatan advisory** — perilakunya sudah benar dan terverifikasi, hanya saran kecil
  (misalnya nama test tidak persis sama dengan kalimat "How to test" di spek) — **tidak**
  memblokir epic.

Kalau QA menemukan ❌/⚠️, epic kembali berstatus `require_fixing` dan Tempa otomatis
menjalankan **sesi perbaikan** (bukan menunggu Anda melakukan apa pun), lalu menjalankan QA
lagi terhadap epic yang sama — berulang sampai lulus bersih. Setiap putaran QA berikutnya
diberi tahu hasil putaran sebelumnya, supaya ia memeriksa ulang temuan lama dulu sebelum
mencari temuan baru.

Pada contoh kita, EPIC-01 (5 feature: scaffold proyek, parsing angka, validasi field, mesin
amortisasi, dan sinkronisasi down-payment) lulus QA di **putaran pertama** — begitu QA
selesai, badge di sebelah nama epic berubah jadi **QA ok**:

![EPIC-01 berstatus Done dengan badge QA ok, bagian QA history bisa dibuka](assets/34-implementation-epic1-qa-passed.png)

Klik **QA history** untuk melihat rincian setiap putaran QA yang pernah dijalankan untuk
epic itu, beserta tanggalnya:

![QA history EPIC-01 dibuka, menampilkan round 1 passed dengan tanggalnya](assets/35-implementation-qa-history.png)

Begitu satu epic lulus, Tempa **langsung lanjut ke epic berikutnya** tanpa jeda — inilah
loop epic → QA → (perbaikan kalau perlu) → epic berikutnya yang berjalan berulang sampai
seluruh rencana selesai, seperti dijelaskan di 6.1.

EPIC-02 (giliran berikutnya) adalah contoh nyata dari siklus **gagal lalu diperbaiki**:
putaran QA pertamanya menandai ❌ pada FEAT-02-05, dan begitu Anda membuka **QA history**,
setiap putaran ditampilkan dengan tanda ✅/❌ beserta tautan **report** ke laporan lengkap
QA-nya. Putaran kedua, setelah sesi perbaikan berjalan otomatis, akhirnya lulus:

![QA history EPIC-02 menampilkan round 1 gagal pada FEAT-02-05 dengan tautan report, round 2 lulus](assets/37-implementation-epic2-qa-2rounds.png)

Anda tidak perlu melakukan apa pun di antara kedua putaran itu — begitu round 1 menandai
❌, Tempa langsung menjalankan sesi perbaikan untuk FEAT-02-05, lalu menjalankan QA lagi,
semuanya dalam satu rangkaian otomatis yang sama.

**Commit otomatis setelah QA lulus** (Settings → Runs → "Version Control" → *Commit after
QA pass*, aktif secara default) — begitu satu epic benar-benar lulus QA, Tempa menjalankan
`git commit` di folder kerja, sehingga proses yang berjalan lama tanpa pengawasan tetap
punya titik pemulihan per-epic alih-alih satu diff raksasa di akhir. Ini dilewati (dicatat,
bukan error) kalau folder kerja bukan repo git — seperti workspace demo kita, yang memang
sengaja dibuat sebagai folder kosong biasa, bukan git repo, supaya fokus panduan ini tetap
di alur Tempa-nya.

### 6.5 Penukaran urutan backlog (dependensi antar-epic)

> Pada PRD contoh kita yang sederhana, keempat epic memang tidak saling bergantung secara
> rumit sehingga skenario ini tidak terjadi selama demo — bagian ini murni penjelasan dari
> perilaku yang didokumentasikan, untuk PRD yang lebih besar/kompleks dengan banyak epic
> saling terkait.

Karena epic dikerjakan **berurutan sesuai rencana**, kadang rencana menempatkan sebuah epic
sebelum epic lain yang sebenarnya jadi *prasyarat*-nya (misalnya epic "Laporan" butuh
fungsi dari epic "Data Transaksi" yang justru dijadwalkan belakangan). Kalau ini terjadi,
sesi implementasi akan menolak "mengakali" arsitektur dan epic itu tampak diam di tempat
(sesi selesai tanpa menambah feature, berulang-ulang).

Tempa mendeteksi ini secara otomatis: kalau sebuah epic sudah 2 sesi berturut-turut selesai
tanpa kemajuan (`implement_no_progress_rounds`, bisa diubah di Settings → Runs), ia meminta
agent menyebutkan **epic mana** yang sebenarnya jadi penghalang, lalu — kalau aman
dilakukan — **menukar urutan** epic tersebut supaya dikerjakan lebih dulu, dan epic yang
tadinya macet otomatis kembali ke antrean setelahnya. Sebuah notifikasi
`implementation_auto_reordered` (kalau email alert diaktifkan) tercatat untuk perubahan ini.

Ini murni penyesuaian **urutan pengerjaan**, bukan menghapus/mengubah isi rencana — kedua
epic tetap dikerjakan penuh, hanya saja epic yang jadi prasyarat dikerjakan duluan. Kalau
Tempa tidak bisa menyelesaikannya sendiri (paling sering: dua epic saling bergantung satu
sama lain / *circular dependency*), epic itu ditandai `failed` dengan penjelasan dari agent
langsung terlihat di kartu Status — dan itu satu-satunya kasus yang benar-benar butuh
keputusan desain dari Anda (gabungkan kedua epic, atau pindahkan sebagian fitur).

### 6.6 Kemungkinan pertanyaan klarifikasi selama implementasi

> Skenario ini juga tidak terjadi pada PRD contoh kita (semua feature-nya cukup
> jelas untuk langsung dikerjakan) — dijelaskan di sini murni dari dokumentasi resmi,
> supaya Anda tahu apa yang akan terlihat kalau ini terjadi pada proyek Anda sendiri.

Berbeda dari sesi yang macet karena dependensi (6.5, yang bisa Tempa perbaiki sendiri), ada
kalanya sebuah **feature** memang butuh **keputusan manusia** — spek yang menyebut fitur
yang ternyata sudah tidak relevan, laporan QA yang merekomendasikan "implementasikan ATAU
hapus fiturnya secara eksplisit", migrasi yang dampaknya perlu izin eksplisit dari Anda.
Ini bukan bug dan tidak akan membaik dengan sesi ulang.

Untuk kasus ini, sesi implementasi menandai **feature** itu `blocked` (bukan epic-nya) dan
menuliskan **pertanyaan** beserta **rekomendasinya**. Epic tetap melanjutkan feature-feature
lain yang tidak terganggu; begitu hanya feature yang `blocked` yang tersisa, epic itu
berubah jadi `deferred` — status ini **tidak menghentikan** runner, epic-epic lain tetap
lanjut berjalan.

Kartu epic yang `deferred` menampilkan pertanyaan dan rekomendasinya lengkap dengan tombol
**Answer…**, yang membuka dialog berisi tiga pilihan: **ikuti rekomendasi**, **tulis jawaban
sendiri**, atau **batalkan fitur ini** (drop) — persis seperti pola menjawab temuan
klarifikasi di bagian 5.3, hanya saja di sini yang dijawab adalah pertanyaan yang muncul
**setelah** kode mulai ditulis. Begitu disimpan, feature itu kembali `require_fixing` dan
epic-nya kembali ke antrean pada putaran berikutnya — sesi implementasi yang mengambilnya
diberi tahu untuk langsung menerapkan keputusan Anda, bukan mempertanyakannya lagi.

### 6.7 Menghentikan proses (dua cara)

Sama seperti Clarification, tombol **Stop Now** adalah tombol gabungan:

- **Stop Now** — mematikan proses beserta CLI backend yang sedang berjalan, seketika.
  Pekerjaan sesi yang sedang berlangsung tapi belum sempat ditulis akan hilang.
- **Stop After Current Session** (panah kecil di sebelah Stop Now) — membiarkan sesi yang
  sedang berjalan selesai dan tersimpan dulu, baru berhenti. Tidak ada token yang terbuang,
  dan proses bisa dilanjutkan kapan saja lewat **Continue Implementation**.

**Gangguan jaringan/limit penggunaan AI tidak menghentikan proses begitu saja** — kalau
limit penggunaan backend tercapai, atau server API-nya melaporkan sedang kelebihan beban,
Tempa menunggu (30 menit untuk limit, 5 menit untuk overload) lalu mencoba lagi secara
otomatis, mengambil pekerjaan tepat dari titik terakhir. Kami sempat mengalami versi lain
dari ini secara langsung selama menyusun panduan ini: proses `claude` sempat auto-update di
tengah sesi sehingga gagal sesaat — begitu update-nya selesai, cukup klik lagi tombol run
yang sama dan prosesnya melanjutkan dari status terakhir tanpa kehilangan apa pun.

Begitu sebuah epic pernah berjalan, tombol berganti nama jadi **Continue Implementation** —
klik ini akan otomatis me-reset epic yang sempat `failed` kembali ke `pending` (persis
seperti `tempa implement --reset-failed`) sebelum melanjutkan, supaya satu kegagalan lama
tidak mengunci tombolnya selamanya.

---

## 7. Setelah Implementasi Selesai

Proses di atas (implement satu/lebih feature → QA → perbaikan kalau perlu → lanjut epic
berikutnya) berulang dengan sendirinya untuk **setiap** epic dalam rencana, tanpa Anda perlu
mengklik apa pun lagi di antaranya — inilah inti dari "start it once, walk away" yang
ditawarkan Tempa. Pada contoh kita, keempat epic (19 feature total) selesai dan lulus QA
dalam waktu sekitar 1 jam 50 menit tanpa campur tangan manual sama sekali setelah tombol
Start Implementation diklik — EPIC-02 sempat butuh satu putaran perbaikan (6.4), tiga epic
lainnya lulus QA di percobaan pertama. Begitu epic terakhir lulus, tab Status menampilkan
semuanya **Done** dengan badge **QA ok**, tombol kembali ke **Continue Implementation**
(tanpa **Stop Now** karena tidak ada lagi yang berjalan), dan **Download Plan** tetap
tersedia:

![Tab Status akhir — keempat epic berstatus Done dengan QA ok](assets/38-implementation-all-done.png)

Tab **Log** mencatat baris penutupnya dengan jelas:

![Tab Log menampilkan baris penutup: All epics done — agent runner stopping / stopped successfully](assets/39-implementation-log-all-done.png)

Runner berhenti otomatis begitu:

- **Semua epic sudah `done` dan lulus QA** — seperti pada contoh kita: implementasi
  dianggap selesai, kode aplikasi ada di folder `src/` workspace Anda, siap
  dijalankan/di-build sesuai tech stack yang ditentukan di PRD.
- **Ada epic yang benar-benar `failed`** (bukan sekadar limit AI/overload, itu ditangani
  otomatis) — perlu perhatian Anda, lihat 6.7.
- **Ada epic yang `deferred`** menunggu jawaban Anda (lihat 6.6) dan tidak ada pekerjaan
  lain yang tersisa.

### 7.1 Memeriksa hasil akhir

- **Download Plan** (tombol di atas tab Status/Log) mengunduh seluruh rencana epic/feature
  sebagai referensi offline.
- Halaman **Verification** (sidebar) menjalankan pemeriksaan manual satu epic terhadap kode
  yang sudah jadi **tanpa mengubah apa pun** — cocok dipakai kalau Anda ingin memeriksa
  ulang satu epic tertentu di luar siklus QA otomatis. Tombol **Verify** juga tersedia
  langsung di kartu tiap epic pada tab Status.
- Dari terminal, `tempa status` menampilkan ringkasan status semua epic/feature/QA yang
  sama seperti tab Status, kalau Anda lebih nyaman lewat command line.
- Kalau **Commit after QA pass** aktif dan workspace-nya adalah git repository sungguhan,
  setiap epic yang lulus QA otomatis tersimpan sebagai satu commit — jadi `git log` di
  workspace Anda menjadi catatan progres per-epic yang rapi.

### 7.2 Kalau ingin melanjutkan lebih jauh

Spesifikasi bertambah/berubah setelah implementasi pertama selesai? Cukup unggah/edit
spesifikasinya lagi (langkah 4), jalankan klarifikasi kalau perlu, lalu klik **Continue
Implementation** — Tempa akan menyusun ulang rencana untuk bagian yang belum
diimplementasikan tanpa mengulang yang sudah jadi.

---

## 8. Rujukan Lanjutan

Panduan ini sengaja berhenti di alur praktik dashboard. Untuk detail lebih dalam tentang
topik-topik yang disinggung di atas, dokumentasi berikut ada di folder [`docs/`](../) repo
ini:

- **Struktur Folder & Path** — apa itu working folder, `workspace.*`, `sources.*`:
  [docs/folders-and-paths.md](../folders-and-paths.md)
- **Menulis Spesifikasi yang Baik** — contoh PRD yang baik, kesalahan umum:
  [docs/writing-a-spec.md](../writing-a-spec.md)
- **Architecture Principles** — aturan lintas-tahap yang disuntikkan ke setiap prompt:
  [docs/architecture-principles.md](../architecture-principles.md)
- **Mode-mode Clarify** (`clarify`, `--auto-answer`, `--apply`, `--finalize`), coverage
  ledger, severity phases secara detail: [docs/clarify-modes.md](../clarify-modes.md)
- **Detail Start Implementation** — siklus status penuh, cross-epic dependency, recovery:
  [docs/start-implementation.md](../start-implementation.md)
- **Backend & Model AI per Tahap** (Claude Code / Copilot CLI / Codex CLI):
  [docs/ai-models.md](../ai-models.md)
- **Ketersediaan CLI Backend** — cara kerja checklist ✅/⬜ di Home/Settings:
  [docs/cli-availability.md](../cli-availability.md)
- **Referensi Perintah CLI** lengkap: [docs/command-reference.md](../command-reference.md)
- **Struktur `config.json`** — setiap key dan fungsinya:
  [docs/config-json.md](../config-json.md)
- **Log & Output** — lokasi setiap jenis log: [docs/logging.md](../logging.md)
- **README.md** di root repo — ringkasan keseluruhan alur CLI maupun dashboard:
  [README.md](../../README.md)

