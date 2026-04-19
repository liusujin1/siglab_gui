  function out_array=resize(in_array,L,W) 
% function out_array=resize(in_array,L,W) 
% resize array to L by W
% Dick Benson DSP Technology
   [l, w] = size(in_array);
   if l==L && w==W
      out_array = in_array;       % no resize needed
   else
      out_array = zeros(L,W);                         % array of zeros
      m = min(size(in_array),size(out_array));        % size of array to copy
      out_array(1:m(1),1:m(2)) = in_array(1:m(1),1:m(2));
   end;
% end function resize

