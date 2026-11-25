//! Flips generated images through the library, in both modes, at every bit
//! depth, with rows that fit in memory, rows that fit in half of it, and rows
//! that do not fit at all. Every case also checks the memory budget.

mod common;

use common::*;

const MEMORY: usize = 4096;

/// Widths giving rows of a few pixels, about a third, two thirds and one and a
/// half times the memory, and a row of more than three times the memory;
/// each with and without unused bits at the end of the row.
fn widths(bpp: u16) -> Vec<u32> {
    let per_kib = 8 * 1024 / u32::from(bpp);
    let mut w = vec![1, 2, 3, 5, 8, 9];
    for kib in [1, 3, 6, 13] {
        w.push(kib * per_kib);
        w.push(kib * per_kib + 3);
    }
    w
}

fn images(bpp: u16) -> Vec<Image> {
    let mut images = Vec::new();
    for width in widths(bpp) {
        for height in [1, 2, 3, 5] {
            let image = Image::new(bpp, width, height);
            images.push(if height == 3 { image.clone().top_down() } else { image.clone() });
            if height == 2 {
                images.push(image.dirty());
            }
        }
    }
    images
}

fn out_of_place(bpp: u16) {
    let images = images(bpp);
    let mut failures = Vec::new();
    for image in &images {
        for op in OPS {
            let what = format!("flip {op:?} {}", image.name());
            if let Err(e) = check(&what, &flip_generated(image, op, MEMORY), MEMORY) {
                failures.push(e);
            }
        }
    }
    all_ok(failures, images.len() * 3);
}

fn in_place(bpp: u16) {
    let images = images(bpp);
    let mut failures = Vec::new();
    for image in &images {
        for op in OPS {
            let what = format!("flip_in_place {op:?} {}", image.name());
            if let Err(e) = check(&what, &flip_in_memory(image, op, MEMORY), MEMORY) {
                failures.push(e);
            }
        }
    }
    all_ok(failures, images.len() * 3);
}

#[test]
fn out_of_place_1bpp() {
    out_of_place(1);
}
#[test]
fn out_of_place_4bpp() {
    out_of_place(4);
}
#[test]
fn out_of_place_8bpp() {
    out_of_place(8);
}
#[test]
fn out_of_place_16bpp() {
    out_of_place(16);
}
#[test]
fn out_of_place_24bpp() {
    out_of_place(24);
}
#[test]
fn out_of_place_32bpp() {
    out_of_place(32);
}
#[test]
fn in_place_1bpp() {
    in_place(1);
}
#[test]
fn in_place_4bpp() {
    in_place(4);
}
#[test]
fn in_place_8bpp() {
    in_place(8);
}
#[test]
fn in_place_16bpp() {
    in_place(16);
}
#[test]
fn in_place_24bpp() {
    in_place(24);
}
#[test]
fn in_place_32bpp() {
    in_place(32);
}

#[test]
fn headers_gaps_and_trailing_bytes() {
    let mut failures = Vec::new();
    let mut total = 0;
    for dib in [40, 52, 56, 108, 124] {
        for (bpp, compression) in [(1, 0), (8, 0), (16, 3), (24, 0), (32, 3)] {
            for (gap, trailing) in [(0, 0), (1024, 37), (5000, 9000)] {
                let image = Image { dib, compression, gap, trailing, ..Image::new(bpp, 1500, 3) };
                for op in OPS {
                    total += 2;
                    let what = format!("{op:?} {}", image.name());
                    failures.extend(check(&format!("flip {what}"), &flip_generated(&image, op, MEMORY), MEMORY).err());
                    failures.extend(check(&format!("flip_in_place {what}"), &flip_in_memory(&image, op, MEMORY), MEMORY).err());
                }
            }
        }
    }
    all_ok(failures, total);
}

#[test]
fn other_budgets() {
    let mut failures = Vec::new();
    let mut total = 0;
    for memory in [4097, 5003, 10_007, 65_536, 100_000] {
        for image in [Image::new(24, 20_000, 3), Image::new(1, 70_001, 4).top_down(), Image::new(8, 333, 77), Image::new(4, 30_001, 2).dirty()] {
            for op in OPS {
                total += 2;
                let what = format!("{op:?} {} with {memory} bytes", image.name());
                failures.extend(check(&format!("flip {what}"), &flip_generated(&image, op, memory), memory).err());
                failures.extend(check(&format!("flip_in_place {what}"), &flip_in_memory(&image, op, memory), memory).err());
            }
        }
    }
    all_ok(failures, total);
}
