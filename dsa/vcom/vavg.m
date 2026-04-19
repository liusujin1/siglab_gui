% computes the average DC value on input channel chan
% Npts is frame size (negative to return average absolute value)
% Ntoss is # of frames to discard (for settling time)

function v=vavg(chan,Npts,Ntoss)

Np = abs(Npts);
for k = 0:Ntoss
  id = siglab('DataReq',Np,chan,'NoWait');   % should be able to use 'Wait'
  while ~siglab('DataRdy',id) drawnow; end;  % wait for data ready
  [d ovld] = siglab('DataGet',id);
end;
v = sum(d)/Np;
if Npts < 0
  for k = 1:length(chan)  d(:,k) = d(:,k) - v(k); end;  % subtract avg value
  v = sum(abs(d))/Np;
end;
%end function vavg
