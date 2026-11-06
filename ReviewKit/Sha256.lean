/-
ReviewKit: SHA-256 (FIPS 180-4), written for the kit so that fingerprints do not depend on
Lean's internal, unversioned `hash` functions. Checked by `review selftest`
against the FIPS test vectors.
-/

namespace ReviewKit.Sha256

private def K : Array UInt32 := #[
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2]

@[inline] private def rotr (x : UInt32) (n : UInt32) : UInt32 :=
  (x >>> n) ||| (x <<< (32 - n))

/-- Pad a message: append 0x80, zeros, and the 64-bit big-endian bit length. -/
private def pad (msg : ByteArray) : ByteArray := Id.run do
  let len := msg.size
  let mut out := msg.push 0x80
  while out.size % 64 != 56 do
    out := out.push 0
  let bits : UInt64 := (len.toUInt64) * 8
  for i in [0:8] do
    out := out.push ((bits >>> ((7 - i).toUInt64 * 8)).toUInt8)
  return out

/-- SHA-256 digest of a byte array (32 bytes). -/
def digest (msg : ByteArray) : ByteArray := Id.run do
  let data := pad msg
  let mut h : Array UInt32 := #[0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]
  let mut w : Array UInt32 := Array.replicate 64 0
  let nBlocks := data.size / 64
  for b in [0:nBlocks] do
    let base := b * 64
    for t in [0:16] do
      let i := base + 4 * t
      w := w.set! t ((data[i]!.toUInt32 <<< 24) ||| (data[i+1]!.toUInt32 <<< 16) |||
        (data[i+2]!.toUInt32 <<< 8) ||| data[i+3]!.toUInt32)
    for t in [16:64] do
      let x := w[t-15]!
      let y := w[t-2]!
      let s0 := rotr x 7 ^^^ rotr x 18 ^^^ (x >>> 3)
      let s1 := rotr y 17 ^^^ rotr y 19 ^^^ (y >>> 10)
      w := w.set! t (w[t-16]! + s0 + w[t-7]! + s1)
    let mut a := h[0]!
    let mut bb := h[1]!
    let mut c := h[2]!
    let mut d := h[3]!
    let mut e := h[4]!
    let mut f := h[5]!
    let mut g := h[6]!
    let mut hh := h[7]!
    for t in [0:64] do
      let S1 := rotr e 6 ^^^ rotr e 11 ^^^ rotr e 25
      let ch := (e &&& f) ^^^ ((~~~ e) &&& g)
      let t1 := hh + S1 + ch + K[t]! + w[t]!
      let S0 := rotr a 2 ^^^ rotr a 13 ^^^ rotr a 22
      let maj := (a &&& bb) ^^^ (a &&& c) ^^^ (bb &&& c)
      let t2 := S0 + maj
      hh := g
      g := f
      f := e
      e := d + t1
      d := c
      c := bb
      bb := a
      a := t1 + t2
    h := #[h[0]! + a, h[1]! + bb, h[2]! + c, h[3]! + d,
      h[4]! + e, h[5]! + f, h[6]! + g, h[7]! + hh]
  let mut out := ByteArray.empty
  for x in h do
    out := out.push (x >>> 24).toUInt8
    out := out.push (x >>> 16).toUInt8
    out := out.push (x >>> 8).toUInt8
    out := out.push x.toUInt8
  return out

private def hexDigit (n : UInt8) : Char :=
  if n < 10 then Char.ofNat (48 + n.toNat) else Char.ofNat (87 + n.toNat)

/-- Lower-case hexadecimal rendering of a byte array. -/
def toHex (bs : ByteArray) : String := Id.run do
  let mut s := ""
  for b in bs do
    s := s.push (hexDigit (b >>> 4))
    s := s.push (hexDigit (b &&& 15))
  return s

/-- `sha256:` followed by the hex digest of the UTF-8 encoding of `s`. -/
def ofString (s : String) : String :=
  "sha256:" ++ toHex (digest s.toUTF8)

/-- The FIPS 180-4 test vectors; `true` when all match. -/
def selfTest : Bool :=
  toHex (digest "".toUTF8) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" &&
  toHex (digest "abc".toUTF8) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad" &&
  toHex (digest "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq".toUTF8) ==
    "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"

end ReviewKit.Sha256
