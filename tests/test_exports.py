"""Styled exports preserve edits, ownership, literal text, fonts, and job data."""

import json
import re
import tempfile
import zlib
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import UUID
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase
from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from fontTools.ttLib import TTFont

from common.http import APIError
from apps.transcriptions.services import exports
from apps.transcriptions.services.queue import JobQueue


class DocumentRenderingTests(SimpleTestCase):
    text = 'SPEAKER_1: Hello team.\n\nزهرا: جلسه امروز درباره پروژه است.\nAli: Release v2 is ready.\n<img src="file:///etc/passwd">'

    def test_word_preserves_literal_text_rtl_and_embedded_fonts(self):
        output = exports.render_export("docx", self.text, "جلسه.wav", 1793610000)
        doc = Document(BytesIO(output))
        self.assertEqual("\n".join(p.text for p in doc.paragraphs[2:]), self.text)
        self.assertEqual(doc.paragraphs[0].style.name, "Title")
        self.assertEqual(
            doc.paragraphs[4]
            ._p.find("w:pPr/w:bidi", doc.paragraphs[4]._p.nsmap)
            .get(qn("w:val")),
            "1",
        )
        with ZipFile(BytesIO(output)) as archive:
            font_table = etree.fromstring(archive.read("word/fontTable.xml"))
            for kind, source in [
                ("Regular", "Vazirmatn-Regular.ttf"),
                ("Bold", "Vazirmatn-Bold.ttf"),
            ]:
                embed = font_table.find(
                    f"w:font[@w:name='Vazirmatn']/w:embed{kind}", font_table.nsmap
                )
                mask = UUID(embed.get(qn("w:fontKey")).strip("{}")).bytes[::-1]
                payload = bytearray(archive.read(f"word/fonts/{kind.lower()}.odttf"))
                for index in range(32):
                    payload[index] ^= mask[index % 16]
                self.assertEqual(bytes(payload), (exports.FONTS / source).read_bytes())
            self.assertIn(b"PAGE", archive.read("word/footer1.xml"))

    def test_pdf_is_real_and_runs_in_a_bounded_worker(self):
        output = exports.generate_export("pdf", self.text, "جلسه.wav", 1793610000)
        self.assertTrue(output.startswith(b"%PDF-"))
        streams = re.findall(rb"stream\n(.*?)\nendstream", output, re.S)
        decoded = []
        for stream in streams:
            try:
                decoded.append(zlib.decompress(stream))
            except zlib.error:
                pass
        self.assertTrue(any(b"/FontFile2" in stream for stream in decoded))
        embedded_fonts = [
            TTFont(BytesIO(stream))
            for stream in decoded
            if stream.startswith(b"\x00\x01\x00\x00")
        ]
        self.assertTrue(embedded_fonts)
        self.assertEqual(
            {font["name"].getDebugName(1) for font in embedded_fonts}, {"Vazirmatn"}
        )

    def test_large_persian_input_reaches_both_workers_without_stalling(self):
        # Exceeds a pipe's capacity even as UTF-8, with long lines as well as
        # normal paragraphs. The old timeout/retry loop stalled before render.
        text = ("زهرا: متن آزمایشی فارسی برای بررسی خروجی جلسه. " * 90 + "\n\n") * 12
        self.assertLess(len(text), exports.MAX_EXPORT_CHARACTERS)
        self.assertGreater(len(text.encode("utf-8")), 65536)
        for format in ("docx", "pdf"):
            with self.subTest(format=format):
                output = exports.generate_export(format, text, "جلسه.wav", 1793610000)
                self.assertTrue(
                    output.startswith(b"PK" if format == "docx" else b"%PDF-")
                )
                if format == "docx":
                    doc = Document(BytesIO(output))
                    self.assertEqual(
                        "\n".join(p.text for p in doc.paragraphs[2:]), text
                    )

    def test_pdf_markup_is_escaped_and_resource_fetches_are_restricted(self):
        with patch("weasyprint.HTML") as html:
            html.return_value.write_pdf.return_value = b"PDF"
            exports.render_export(
                "pdf", self.text, '<img src="https://example.test/x">.wav', 1793610000
            )
        markup = html.call_args.kwargs["string"]
        self.assertNotIn("<img src=", markup)
        self.assertIn("&lt;img src=", markup)
        fetch = html.call_args.kwargs["url_fetcher"]
        resource = fetch("https://export.invalid/Vazirmatn-Regular.ttf")
        self.assertEqual(
            resource.read(), (exports.FONTS / "Vazirmatn-Regular.ttf").read_bytes()
        )
        resource.close()
        for url in [
            "file:///etc/passwd",
            "https://example.test/",
            "data:text/plain,secret",
        ]:
            with self.assertRaises(ValueError):
                fetch(url)

    def test_worker_word_timeout_failure_and_busy_release_the_slot(self):
        output = exports.generate_export("docx", self.text, "meeting.wav", 1793610000)
        self.assertTrue(output.startswith(b"PK"))
        for error, status in [
            (TimeoutError(), 504),
            (RuntimeError("private"), 503),
            (ValueError("private"), 503),
        ]:
            with patch.object(exports, "run_isolated", side_effect=error):
                with self.assertRaises(APIError) as caught:
                    exports.generate_export("pdf", "Text", "meeting.wav", 1)
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("private", caught.exception.detail)
            self.assertFalse(exports._export_lock.locked())
        exports._export_lock.acquire()
        try:
            with self.assertRaises(APIError) as caught:
                exports.generate_export("pdf", "Text", "meeting.wav", 1)
            self.assertEqual(caught.exception.status_code, 429)
        finally:
            exports._export_lock.release()

    def test_filenames_and_direction(self):
        self.assertEqual(
            exports.export_filename("../جلسه\n.wav", "pdf"), "جلسه-transcript.pdf"
        )
        self.assertEqual(
            exports.export_filename("C:\\folder\\meeting.mp3", "docx"),
            "meeting-transcript.docx",
        )
        self.assertEqual(exports.direction("123 جلسه"), "rtl")
        self.assertEqual(exports.direction("English and فارسی"), "ltr")


class ExportAPITests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.queue = JobQueue(Path(directory.name), lambda *a, **kw: "Saved transcript")
        self.user = get_user_model().objects.create_user("export-owner")
        self.client.force_login(self.user)
        self.owner = f"user:{self.user.pk}"
        queue_patch = patch(
            "apps.transcriptions.views.get_queue", return_value=self.queue
        )
        queue_patch.start()
        self.addCleanup(queue_patch.stop)
        source = self.queue.uploads / "source.wav"
        source.write_bytes(b"fake audio")
        self.queue.enqueue(
            "export-job",
            self.owner,
            "جلسه.wav",
            source,
            {
                "model": "fake",
                "fallback_model": "",
                "chunk_minutes": 0,
                "language_hint": "",
                "prompt": "Original prompt",
            },
        )
        self.queue._process(self.queue._claim())
        self.url = "/jobs/export-job/export"

    def post(self, body, **kwargs):
        return self.client.post(
            self.url, json.dumps(body), content_type="application/json", **kwargs
        )

    def test_exports_edited_or_saved_text_without_mutating_history(self):
        before = self.queue.get("export-job", self.owner)
        with patch.object(exports, "generate_export", return_value=b"file") as generate:
            response = self.post({"format": "docx", "text": "Edited فارسی text"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(generate.call_args.args[1], "Edited فارسی text")
            self.assertEqual(response["Content-Type"], exports.EXPORT_TYPES["docx"])
            self.assertIn("filename*=utf-8''", response["Content-Disposition"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(self.post({"format": "pdf"}).status_code, 200)
            self.assertEqual(generate.call_args.args[1], "Saved transcript")
        self.assertEqual(
            self.queue.result("export-job", self.owner), "Saved transcript"
        )
        self.assertEqual(self.queue.get("export-job", self.owner), before)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_login_ownership_completion_and_origin(self):
        self.assertEqual(
            Client().post(self.url, "{}", content_type="application/json").status_code,
            401,
        )
        other = get_user_model().objects.create_user("another-export-user")
        self.client.force_login(other)
        self.assertEqual(self.post({"format": "pdf"}).status_code, 404)
        self.client.force_login(self.user)
        self.assertEqual(
            self.post({"format": "pdf"}, HTTP_ORIGIN="https://other.test").status_code,
            403,
        )
        with self.queue._db() as db:
            db.execute("UPDATE jobs SET status='processing' WHERE id='export-job'")
        self.assertEqual(self.post({"format": "pdf"}).status_code, 409)

    def test_invalid_text_format_and_size_never_generate(self):
        cases = [
            {},
            [],
            {"format": []},
            {"format": "html"},
            {"format": "pdf", "text": None},
            {"format": "pdf", "text": ""},
            {"format": "pdf", "text": "  "},
            {"format": "pdf", "text": "a" * 100001},
            {"format": "pdf", "text": "\x00"},
            {"format": "docx", "text": "\ud800"},
            {"format": "docx", "text": "\uffff"},
            {"format": "pdf", "text": "Text", "url": "file:///etc/passwd"},
        ]
        with patch.object(exports, "generate_export") as generate:
            for body in cases:
                self.assertEqual(self.post(body).status_code, 422, str(body)[:60])
            self.assertEqual(
                self.client.post(
                    self.url, "{", content_type="application/json"
                ).status_code,
                422,
            )
            generate.assert_not_called()
