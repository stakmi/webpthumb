"""CLI convert exit codes and output names."""

from __future__ import annotations

from pathlib import Path

from webpthumb.cli import main

from samples import heif_bytes, pdf_bytes, png_bytes


def test_convert_png_and_pdf(tmp_path: Path) -> None:
    source = tmp_path / "in"
    source.mkdir()
    (source / "photo.png").write_bytes(png_bytes((120, 40)))
    (source / "doc.pdf").write_bytes(pdf_bytes(pages=2))
    output = tmp_path / "out"
    code = main([
        "convert",
        str(source),
        "-o",
        str(output),
        "-r",
        "-w",
        "32",
        "-j",
        "1",
        "-p",
        "all",
    ])
    assert code == 0
    assert (output / "photo.webp").is_file()
    assert (output / "doc_p1.webp").is_file()
    assert (output / "doc_p2.webp").is_file()


def test_convert_heic(tmp_path: Path) -> None:
    photo = tmp_path / "photo.heic"
    photo.write_bytes(heif_bytes((120, 40)))
    output = tmp_path / "out"
    code = main(["convert", str(photo), "-o", str(output), "-w", "32", "-j", "1"])
    assert code == 0
    dest = output / "photo.webp"
    assert dest.is_file()
    assert dest.read_bytes()[8:12] == b"WEBP"


def test_convert_unsupported_file_exits_1(tmp_path: Path) -> None:
    note = tmp_path / "note.txt"
    note.write_bytes(b"hello")
    code = main(["convert", str(note), "-o", str(tmp_path / "out"), "-j", "1"])
    assert code == 1


def test_convert_bad_width_exits_2(tmp_path: Path) -> None:
    photo = tmp_path / "a.png"
    photo.write_bytes(png_bytes((10, 10)))
    code = main(["convert", str(photo), "-o", str(tmp_path / "out"), "-w", "0"])
    assert code == 2


def test_convert_skips_existing_thumbnail(tmp_path: Path) -> None:
    photo = tmp_path / "a.png"
    photo.write_bytes(png_bytes((30, 30)))
    output = tmp_path / "out"
    assert main(["convert", str(photo), "-o", str(output), "-w", "16", "-j", "1"]) == 0
    stamp = (output / "a.webp").stat().st_mtime_ns
    assert main(["convert", str(photo), "-o", str(output), "-w", "16", "-j", "1"]) == 0
    assert (output / "a.webp").stat().st_mtime_ns == stamp
