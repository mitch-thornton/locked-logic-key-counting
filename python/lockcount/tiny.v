// tiny locked netlist, structural
module tiny (a, b, c, keyinput0, keyinput1, y);
  input a, b, c, keyinput0, keyinput1;
  output y;
  wire w0, w1, w2;
  xor (w0, a, keyinput0);
  xor (w1, b, keyinput1);
  and (w2, w0, w1);
  or  (y, w2, c);
endmodule
